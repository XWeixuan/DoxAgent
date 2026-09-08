"""Case and invocation history stay tied to their ticker and frozen read watermark."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from doxagent.v2_read.metrics import Metrics

from .errors import ApiFailure


def install(app: FastAPI) -> None:
    prefix = "/api/doxagent/v2/tickers/{ticker}/runtime"
    store, views, query, respond = (
        app.state.store,
        app.state.views,
        app.state.query,
        app.state.respond,
    )

    def require_case(ticker: str, identity: str, seq: int | None = None) -> dict:
        value = store.get("case", ticker, identity, seq)
        if value is None:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return value

    @app.get(prefix + "/metrics")
    async def metrics(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id"})
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "RUNTIME":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        period = view["wire"]["period"]
        days = None if period["selected"] == "ALL" else period["current"]["trading_days"]
        previous = period["previous"]["trading_days"] if period["previous"] else None
        service = Metrics(store)
        values = {
            name: service.metric(name, [ticker], view["seq"], days=days, previous_days=previous)
            for name in (
                "processed_cases",
                "new_cases",
                "old_cases",
                "hit_cases",
                "w3_cases",
                "new_fact_candidates",
                "executed_cases",
            )
        }
        for name, numerator, denominator, unit in (
            ("hit_case_ratio", "hit_cases", "policy_decided_cases", "RATIO"),
            ("hot_path_mean_seconds", "hot_path_seconds", "hot_path_samples", "SECONDS"),
            ("w3_mean_seconds", "w3_seconds", "w3_samples", "SECONDS"),
        ):
            values[name] = service.quotient(
                name,
                numerator,
                denominator,
                [ticker],
                view["seq"],
                days=days,
                previous_days=previous,
                unit=unit,
                comparison_applicable=period["comparison_applicable"],
            )
        return respond(request, "RuntimeMetrics", values, view_id=args["view_id"])

    @app.get(prefix + "/cases")
    async def cases(ticker: str, request: Request) -> Any:
        args = query(request, {"view_id", "limit", "cursor", "result", "source_id"})
        owner = request.state.principal.user_id
        view = views.get(owner, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "RUNTIME":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        result = args.get("result")
        if result and result not in {
            "ARCHIVE",
            "EVENT_DISCOVERY",
            "BADCASE",
            "TRADE_EXECUTION",
            "FAILURE",
        }:
            raise ApiFailure("VALIDATION_FAILED", 422)
        period = view["wire"]["period"]
        days = period["current"]["trading_days"] if period and period["selected"] != "ALL" else None
        value = views.page(
            owner,
            "case",
            ticker,
            view=view,
            view_id=args["view_id"],
            days=days,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
            result=result,
            source_id=args.get("source_id"),
        )
        return respond(request, "CaseSummaryPage", value, view_id=args["view_id"])

    @app.get(prefix + "/cases/{case_id}/attempts")
    async def attempts(ticker: str, case_id: str, request: Request) -> Any:
        args = query(request, {"node", "limit", "cursor"})
        require_case(ticker, case_id)
        if args.get("node") not in {"W1", "W2", "W3"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        value = views.page(
            request.state.principal.user_id,
            "attempt",
            ticker,
            view=None,
            parent=case_id,
            route=args["node"],
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(request, "ModelAttemptPage", value)

    @app.get(prefix + "/cases/{case_id}/messages")
    async def messages(ticker: str, case_id: str, request: Request) -> Any:
        args = query(request, {"view_id", "limit", "cursor"})
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        require_case(ticker, case_id, view["seq"])
        value = views.page(
            request.state.principal.user_id,
            "message",
            ticker,
            view=view,
            view_id=args["view_id"],
            parent=case_id,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(request, "MessageSummaryPage", value, view_id=args["view_id"])

    @app.get(prefix + "/cases/{case_id}/candidates")
    async def candidates(ticker: str, case_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        require_case(ticker, case_id)
        value = views.page(
            request.state.principal.user_id,
            "candidate",
            ticker,
            view=None,
            parent=case_id,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(request, "CandidatePage", value)
