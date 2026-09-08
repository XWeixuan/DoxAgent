"""Pinned Policy and Event Library reads from local immutable indexes."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from .dto import available
from .errors import ApiFailure


def install(app: FastAPI) -> None:
    prefix = "/api/doxagent/v2/tickers/{ticker}"
    store, views, query, respond = (
        app.state.store,
        app.state.views,
        app.state.query,
        app.state.respond,
    )

    def active(request: Request, ticker: str, args: dict[str, str], page: str) -> tuple[dict, dict]:
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != page:
            raise ApiFailure("SCOPE_MISMATCH", 400)
        value = view["wire"]["activation"]["data"]
        if value is None:
            raise ApiFailure("NO_ACTIVE_REVISION", 404)
        return view, value

    def page(
        request: Request,
        ticker: str,
        kind: str,
        parent: str,
        args: dict[str, str],
        view: dict | None = None,
    ) -> dict:
        return views.page(
            request.state.principal.user_id,
            kind,
            ticker,
            parent=parent,
            view=view,
            view_id=args.get("view_id"),
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )

    def policy_shells(
        request: Request, ticker: str, args: dict[str, str], view: dict, activation: dict
    ) -> dict:
        value = page(request, ticker, "shell", activation["document2"]["run_id"], args, view)
        value["items"] = [
            {key: item[key] for key in ("shell_id", "ordinal", "core_question")}
            for item in value["items"]
        ]
        return value

    @app.get(prefix + "/policies/context")
    async def context(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id", "limit"})
        view, activation = active(request, ticker, args, "POLICIES")
        policies = store.get(
            "policy_set", ticker, activation["policy_set"]["artifact_id"], view["seq"]
        )
        if not policies:
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 404)
        shells = policy_shells(request, ticker, args, view, activation)
        value = {
            "runtime_activation_id": activation["runtime_activation_id"],
            "document2": activation["document2"],
            "policy_set": activation["policy_set"],
            "source_document2": policies["source_document2"],
            "source_context_consistent": available(
                activation["document2"] == policies["source_document2"]
            ),
            "shells": shells,
            "default_shell_id": shells["items"][0]["shell_id"] if shells["items"] else None,
        }
        return respond(request, "PolicyContext", value, view_id=args["view_id"])

    @app.get(prefix + "/policies/shells")
    async def shells(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id", "cursor", "limit"})
        view, activation = active(request, ticker, args, "POLICIES")
        value = policy_shells(request, ticker, args, view, activation)
        return respond(request, "PolicyShellSummaryPage", value, view_id=args["view_id"])

    @app.get(prefix + "/policies/{policy_id}/revisions/{policy_revision_id}")
    async def policy(ticker: str, policy_id: str, policy_revision_id: str, request: Request) -> Any:
        args = query(request, {"policy_set_version", "view_id"})
        view = (
            views.get(request.state.principal.user_id, args["view_id"], ticker)
            if args.get("view_id")
            else None
        )
        value = store.get(
            "policy_detail", ticker, policy_revision_id, view["seq"] if view else None
        )
        if not value or value["summary"]["policy_id"] != policy_id:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        if str(value["summary"]["policy_set_version"]) != args.get("policy_set_version"):
            raise ApiFailure("SCOPE_MISMATCH", 400)
        summary = value["summary"]
        catalog = store.get(
            "policy_catalog",
            ticker,
            policy_id + ":" + summary["policy_activation_revision"],
            view["seq"] if view else None,
        )
        if catalog:
            for name in ("consumed", "consumed_at", "effective", "lifecycle"):
                summary[name] = catalog[name]
        return respond(request, "PolicyDetail", value, view_id=args.get("view_id"))

    @app.get(prefix + "/policy-sets/{policy_set_version}/download")
    async def policy_download(ticker: str, policy_set_version: int, request: Request) -> Any:
        args = query(request, {"runtime_activation_id"})
        activation = store.get("activation_revision", ticker, args.get("runtime_activation_id", ""))
        if not activation or activation["policy_set"]["policy_set_version"] != policy_set_version:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        value = store.get("policy_set", ticker, activation["policy_set"]["artifact_id"])
        if value is None:
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 404)
        ref = value["content"]

        def chunks() -> Any:
            offset = 0
            while offset < ref["size_bytes"]:
                _, text, end = store.content(ticker, ref["content_id"], offset, 65536)
                if end <= offset:
                    raise ValueError("corrupt indexed content")
                yield text.encode("utf-8")
                offset = end

        return StreamingResponse(
            chunks(),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="policy-set.json"',
                "ETag": '"' + ref["sha256"] + '"',
                "Cache-Control": "private, no-store",
            },
        )

    @app.get(prefix + "/event-library/current")
    async def library(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id"})
        _, activation = active(request, ticker, args, "EVENTS")
        return respond(request, "LibraryRef", activation["event_library"], view_id=args["view_id"])

    def detail(
        request: Request, ticker: str, snapshot: str, event_id: str, args: dict[str, str]
    ) -> Any:
        identity = snapshot + ":" + event_id
        value = store.get("event_detail", ticker, identity)
        if value is None:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        value["facts"] = page(request, ticker, "fact", identity, args)
        return respond(request, "EventDetail", value)

    @app.get(prefix + "/event-library/snapshots/{library_snapshot_id}/events/{event_id}")
    async def event(ticker: str, library_snapshot_id: str, event_id: str, request: Request) -> Any:
        args = query(request, {"limit"})
        return detail(request, ticker, library_snapshot_id, event_id, args)

    @app.get(prefix + "/event-library/snapshots/{library_snapshot_id}/events/{event_id}/facts")
    async def facts(ticker: str, library_snapshot_id: str, event_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        identity = library_snapshot_id + ":" + event_id
        if not store.get("event_detail", ticker, identity):
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return respond(request, "FactRowPage", page(request, ticker, "fact", identity, args))
