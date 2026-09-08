from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from .errors import ApiFailure

PREFIX = "/api/doxagent/v2"


def install(app: FastAPI) -> None:
    store, views = app.state.store, app.state.views
    query, respond = app.state.query, app.state.respond

    def chunk(request: Request, ticker: str, identity: str, cursor: str | None) -> Any:
        offset, index = 0, 0
        scope = "content:" + ticker + ":" + identity
        if cursor:
            try:
                token = store.token(cursor, request.state.principal.user_id, scope=scope)
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
            offset, index = token["offset"], token["index"]
        try:
            reference, text, end = store.content(ticker, identity, offset=offset, size=16000)
        except KeyError:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404) from None
        complete = end >= reference["size_bytes"]
        next_cursor = (
            None
            if complete
            else store.save_token(
                request.state.principal.user_id, scope, {"offset": end, "index": index + 1}
            )
        )
        return respond(
            request,
            "ContentChunk",
            {
                "content": reference,
                "chunk_index": index,
                "text": text,
                "next_cursor": next_cursor,
                "complete": complete,
            },
        )

    app.state.content_chunk = chunk

    @app.get(PREFIX + "/tickers/{ticker}/contents/{content_id}")
    async def content(ticker: str, content_id: str, request: Request) -> Any:
        args = query(request, {"cursor"})
        return chunk(request, ticker, content_id, args.get("cursor"))

    @app.get(PREFIX + "/tickers/{ticker}/contents/{content_id}/citations")
    async def citations(ticker: str, content_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        try:
            store.content(ticker, content_id, size=1)
        except KeyError:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404) from None
        page = views.page(
            request.state.principal.user_id,
            "citation",
            ticker,
            view=None,
            parent=content_id,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(request, "CitationPage", page)

    @app.get(PREFIX + "/tickers/{ticker}/research/runs/{run_id}/sections/{section}")
    async def research_section(ticker: str, run_id: str, section: str, request: Request) -> Any:
        args = query(request, {"cursor"})
        if section not in {"C1", "C3", "C5"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        reference = store.get("research_section", ticker, run_id + ":" + section)
        if not reference:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return chunk(request, ticker, reference["content_id"], args.get("cursor"))

    @app.get(PREFIX + "/tickers/{ticker}/research/runs/{run_id}/future-nodes")
    async def future_nodes(ticker: str, run_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        if not store.get("research", ticker, run_id):
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        page = views.page(
            request.state.principal.user_id,
            "future_node",
            ticker,
            view=None,
            parent=run_id,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(request, "FutureNodeRowPage", page)

    @app.get(PREFIX + "/tickers/{ticker}/expectations/runs/{run_id}/shells")
    async def shells(ticker: str, run_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        if not store.get("expectations_index", ticker, run_id):
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        page = views.page(
            request.state.principal.user_id,
            "shell",
            ticker,
            view=None,
            parent=run_id,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(request, "ShellSummaryPage", page)

    @app.get(PREFIX + "/tickers/{ticker}/expectations/runs/{run_id}/shells/{shell_id}/units")
    async def units(ticker: str, run_id: str, shell_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        parent = run_id + ":" + shell_id
        index = store.get("expectations_index", ticker, run_id)
        if not index or not store.get("shell", ticker, parent):
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        page = views.page(
            request.state.principal.user_id,
            "unit",
            ticker,
            view=None,
            parent=parent,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(
            request,
            "ShellContent",
            {
                "run_id": run_id,
                "shell_id": shell_id,
                "content_status": "PUBLISHED_PARTIAL"
                if index["publication_state"] == "PARTIAL"
                else "PUBLISHED_COMPLETE",
                "units": page,
            },
        )

    @app.get(
        PREFIX
        + "/tickers/{ticker}/expectations/runs/{run_id}/shells/{shell_id}/units/{expectation_id}"
    )
    async def unit(
        ticker: str, run_id: str, shell_id: str, expectation_id: str, request: Request
    ) -> Any:
        query(request, set())
        value = store.get("unit", ticker, run_id + ":" + shell_id + ":" + expectation_id)
        if not value:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return respond(request, "ExpectationUnit", value)

    def summary(
        request: Request,
        ticker: str,
        run_id: str,
        kind: str,
        *,
        view: dict[str, Any] | None = None,
        view_id: str | None = None,
        limit: int = 20,
    ) -> Any:
        seq = view["seq"] if view else None
        if kind == "research":
            value = store.get(kind, ticker, run_id, seq)
            if not value:
                raise ApiFailure("RESOURCE_NOT_FOUND", 404)
            value["run"] = store.get("research_run", ticker, run_id, seq) or value["run"]
            return respond(request, "ResearchSummary", value, view_id=view_id)
        index = store.get("expectations_index", ticker, run_id, seq)
        if not index:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        index["run"] = store.get("expectations_run", ticker, run_id, seq) or index["run"]
        index.pop("publication_state")
        index["shells"] = views.page(
            request.state.principal.user_id,
            "shell",
            ticker,
            view=view,
            view_id=view_id,
            parent=run_id,
            limit=limit,
        )
        if view and view["wire"]["activation"]["data"]:
            index["runtime_activation_id"] = view["wire"]["activation"]["data"][
                "runtime_activation_id"
            ]
        return respond(request, "ExpectationsSummary", index, view_id=view_id)

    @app.get(PREFIX + "/tickers/{ticker}/research/current")
    async def research_current(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id"})
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        active = view["wire"]["activation"]["data"]
        if not active:
            raise ApiFailure("NO_ACTIVE_REVISION", 404)
        return summary(
            request,
            ticker,
            active["document1"]["run_id"],
            "research",
            view=view,
            view_id=args["view_id"],
        )

    @app.get(PREFIX + "/tickers/{ticker}/expectations/current")
    async def expectations_current(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id", "limit"})
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        active = view["wire"]["activation"]["data"]
        if not active:
            raise ApiFailure("NO_ACTIVE_REVISION", 404)
        return summary(
            request,
            ticker,
            active["document2"]["run_id"],
            "expectations",
            view=view,
            view_id=args["view_id"],
            limit=int(args.get("limit", 20)),
        )

    @app.get(PREFIX + "/tickers/{ticker}/research/runs/{run_id}")
    async def research_run(ticker: str, run_id: str, request: Request) -> Any:
        query(request, set())
        return summary(request, ticker, run_id, "research")

    @app.get(PREFIX + "/tickers/{ticker}/expectations/runs/{run_id}")
    async def expectations_run(ticker: str, run_id: str, request: Request) -> Any:
        args = query(request, {"limit"})
        return summary(request, ticker, run_id, "expectations", limit=int(args.get("limit", 20)))

    @app.get(PREFIX + "/tickers/{ticker}/research/runs")
    async def research_runs(ticker: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        return respond(
            request,
            "RunSummaryPage",
            views.page(
                request.state.principal.user_id,
                "research_run",
                ticker,
                view=None,
                limit=int(args.get("limit", 20)),
                cursor=args.get("cursor"),
            ),
        )

    @app.get(PREFIX + "/tickers/{ticker}/expectations/runs")
    async def expectation_runs(ticker: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        return respond(
            request,
            "RunSummaryPage",
            views.page(
                request.state.principal.user_id,
                "expectations_run",
                ticker,
                view=None,
                limit=int(args.get("limit", 20)),
                cursor=args.get("cursor"),
            ),
        )
