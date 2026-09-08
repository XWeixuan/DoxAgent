"""Control navigation, initialization progress and calendar endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request

from doxagent.semantic_clock import boundary
from doxagent.v2_read.repository import instant

from .dto import available, coverage, missing
from .errors import ApiFailure


def install(app: FastAPI) -> None:
    prefix = "/api/doxagent/v2"
    store, views, control = app.state.store, app.state.views, app.state.control
    query, respond = app.state.query, app.state.respond

    def overview_view(request, args):
        view = views.get(request.state.principal.user_id, args.get("view_id", ""))
        if view["wire"]["page"] != "OVERVIEW":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        return view

    def measures(view, tickers, names):
        from doxagent.v2_read.metrics import Metrics

        period = view["wire"]["period"]
        previous = period["previous"]
        service = Metrics(store)
        return {
            name: service.metric(
                name,
                tickers,
                view["seq"],
                days=period["current"]["trading_days"],
                previous_days=previous["trading_days"] if previous else None,
                unit="USD" if name.endswith("pnl") or name == "api_token_cost" else "COUNT",
                distinct=name == "policy_hits",
            )
            for name in names
        }

    @app.get(prefix + "/overview/metrics")
    async def overview_metrics(request: Request):
        args = query(request, {"view_id"})
        view = overview_view(request, args)
        data = measures(
            view,
            view["tickers"],
            (
                "policy_hits",
                "trade_executed",
                "trade_triggered",
                "messages",
                "api_token_cost",
                "nonroutine_repairs",
                "paper_realized_net_pnl",
                "live_realized_net_pnl",
            ),
        )
        data["nonroutine_repairs"].update(
            current=available("0"), previous=missing("NOT_APPLICABLE", "NOT_APPLICABLE"),
            change_pct=missing("NOT_APPLICABLE", "NOT_APPLICABLE"),
            current_coverage=coverage(complete=True), previous_coverage=None, provisional=False,
        )
        return respond(request, "OverviewMetrics", data, view_id=args["view_id"])

    @app.get(prefix + "/overview/tickers")
    async def overview_tickers(request: Request):
        from .bus import aggregates

        args = query(request, {"view_id", "limit", "cursor", "run_state", "health"})
        view = overview_view(request, args)
        owner = request.state.principal.user_id
        try:
            limit = int(args.get("limit", 20))
            if not 1 <= limit <= 100:
                raise ValueError()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        run_state, health = args.get("run_state"), args.get("health")
        if run_state is not None and run_state not in {"INITIALIZING", "RUNNING", "PAUSED", "STOPPED"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        if health is not None and health not in {"NORMAL", "DEGRADED", "BLOCKED", "UNKNOWN"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        scope = f"overview:{args['view_id']}:{limit}:{run_state}:{health}"
        after = ""
        if args.get("cursor"):
            try:
                after = store.token(args["cursor"], owner, scope=scope)["after"]
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
        # Filter the frozen candidate set before pagination; never filter a returned page.
        with store.connect() as db:
            selected = [row[0] for row in db.execute(
                "SELECT ticker FROM objects WHERE kind='ticker' AND id=ticker "
                "AND ticker IN (SELECT value FROM json_each(?)) AND ticker>? "
                "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
                "AND (? IS NULL OR json_extract(payload,'$.run_state')=?) "
                "AND (? IS NULL OR json_extract(payload,'$.health')=?) ORDER BY ticker LIMIT ?",
                (json.dumps(view["tickers"]), after, view["seq"], view["seq"], run_state, run_state, health, health, limit+1),
            )]
        items = []
        for ticker in selected[:limit]:
            state = store.get("ticker", ticker, ticker, view["seq"])
            latest = store.page("message", ticker, view["seq"], limit=1)
            initialization = (
                store.get("initialization", ticker, state["initialization_id"], view["seq"])
                if state["initialization_id"]
                else None
            )
            items.append(
                {
                    "state": state,
                    "last_standard_message_at": available(latest[0]["data"]["stream_published_at"])
                    if latest
                    else missing(),
                    "source_counts": aggregates(store, ticker, view["seq"])[0],
                    "metrics": measures(
                        view,
                        [ticker],
                        ("trade_executed", "trade_triggered", "messages", "api_token_cost"),
                    ),
                    "initialization": {
                        "state": "AVAILABLE" if initialization else "UNAVAILABLE",
                        "data": initialization,
                        "reason": None if initialization else "SOURCE_GAP",
                        "coverage": coverage(),
                    }
                    if state["initialization_incomplete"]
                    else None,
                }
            )
        more = len(selected) > limit
        cursor = store.save_token(owner, scope, {"after": selected[limit - 1]}) if more else None
        return respond(
            request,
            "TickerOverviewPage",
            {
                "items": items,
                "limit": limit,
                "has_more": more,
                "next_cursor": cursor,
                "snapshot_id": args["view_id"],
            },
            view_id=args["view_id"],
        )

    @app.get(prefix + "/tickers")
    async def tickers(request: Request) -> Any:
        args = query(request, {"visibility", "limit", "cursor"})
        if args.get("visibility", "NAVIGATION") != "NAVIGATION":
            raise ApiFailure("VALIDATION_FAILED", 422)
        value = views.page(
            request.state.principal.user_id,
            "navigation",
            "",
            view=None,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return respond(request, "TickerNavigationPage", value)

    @app.get(prefix + "/tickers/{ticker}/initializations/{initialization_id}")
    async def initialization(ticker: str, initialization_id: str, request: Request) -> Any:
        query(request, set())
        value = store.get("initialization", ticker, initialization_id)
        if value is None:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        state = control.get(ticker)
        if state:
            value["control_etag"] = '"' + str(state["revision"]) + '"'
            value["manual_resume_allowed"] &= not state["removed"]
        return respond(request, "InitializationProgress", value)

    @app.get(prefix + "/capabilities")
    async def capabilities(request: Request) -> Any:
        args = query(request, {"ticker"})
        ticker = args.get("ticker")
        if ticker and control.get(ticker) is None:
            raise ApiFailure("TICKER_NOT_FOUND", 404)
        with control.read() as db:
            bindings = {
                r[0]
                for r in db.execute(
                    "SELECT mode FROM v2_mode_binding WHERE ticker=?", (ticker or "",)
                )
            }
            heartbeat = db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='v2_workers' AND key='control'"
            ).fetchone()
        import json
        from datetime import UTC

        fresh = bool(
            heartbeat
            and (
                datetime.now(UTC) - datetime.fromisoformat(json.loads(heartbeat[0])["heartbeat_at"])
            ).total_seconds()
            < 30
        )

        def capability(ok: bool, reason: str) -> dict[str, Any]:
            return {"available": ok, "reason": None if ok else reason, "message": None}

        with store.connect() as db:
            source_ready = {
                row[0]: not row[3]
                and row[1] >= row[2]
                and (datetime.now(UTC) - datetime.fromisoformat(row[4])).total_seconds() < 30
                for row in db.execute(
                    "SELECT source,checkpoint,head,error,checked_at FROM source_health"
                )
            }
        value = {
            "monitoring": capability(fresh, "CONTROL_WORKER_UNAVAILABLE"),
            "paper_trading": capability(fresh and "PAPER_TRADING" in bindings, "MODE_UNAVAILABLE"),
            "live_trading": capability(fresh and "LIVE_TRADING" in bindings, "MODE_UNAVAILABLE"),
            "revenue_audit": capability(
                False,
                "NOT_RELEASED",
            ),
            "message_stream": capability(
                source_ready.get("bus", False), "BUS_PROJECTION_UNAVAILABLE"
            ),
            "runtime_graph_stream": capability(
                source_ready.get("runtime", False), "GRAPH_PROJECTION_UNAVAILABLE"
            ),
        }
        return respond(request, "Capabilities", value)

    @app.get(prefix + "/calendar/trading-days")
    async def calendar(request: Request) -> Any:
        args = query(request, {"view_id", "start_day", "end_day", "limit", "cursor"})
        owner = request.state.principal.user_id
        view = views.get(owner, args.get("view_id", ""))
        try:
            start, end = date.fromisoformat(args["start_day"]), date.fromisoformat(args["end_day"])
            limit = int(args.get("limit", 20))
            if start > end or not 1 <= limit <= 100 or (end - start).days > 36600:
                raise ValueError("invalid range")
        except (ValueError, KeyError):
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        scope = f"calendar:{args['view_id']}:{start}:{end}:{limit}"
        day = start
        if args.get("cursor"):
            try:
                day = date.fromisoformat(store.token(args["cursor"], owner, scope=scope)["day"])
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
        items = []
        while day <= end and len(items) <= limit:
            bounds = views.calendar.calendar.session_bounds(day)
            if bounds:
                items.append(
                    {
                        "day": str(day),
                        "start_at": instant(boundary(day)),
                        "end_at": instant(boundary(day + timedelta(days=1))),
                        "market_open_at": instant(bounds[0]),
                        "market_close_at": instant(bounds[1]),
                    }
                )
            day += timedelta(days=1)
        more = len(items) > limit
        cursor = store.save_token(owner, scope, {"day": items[-1]["day"]}) if more else None
        return respond(
            request,
            "TradingDayPage",
            {
                "items": items[:limit],
                "limit": limit,
                "has_more": more,
                "next_cursor": cursor,
                "snapshot_id": args["view_id"],
            },
            view_id=view["wire"]["view_id"],
        )

    @app.get(prefix + "/overview/status")
    async def overview_status(request: Request) -> Any:
        args = query(request, {"view_id"})
        view = views.get(request.state.principal.user_id, args.get("view_id", ""))
        if view["wire"]["page"] != "OVERVIEW":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        states = [store.get("ticker", ticker, ticker, view["seq"]) for ticker in view["tickers"]]
        states = [
            s
            for s in states
            if s
            and s["run_state"] == "RUNNING"
            and not s["initialization_incomplete"]
            and not s["removed"]
        ]
        return respond(
            request,
            "OverviewStatus",
            {
                "clock": view["wire"]["clock"],
                "normal_tickers": available(
                    sum(s["health"] in {"NORMAL", "DEGRADED"} for s in states)
                ),
                "blocked_tickers": available(sum(s["health"] == "BLOCKED" for s in states if s)),
            },
            view_id=args["view_id"],
            resource_coverage=coverage(
                complete=all(s["health"] != "UNKNOWN" for s in states),
                count=sum(s["health"] != "UNKNOWN" for s in states),
                reasons=["HEALTH_UNKNOWN"] if any(s["health"] == "UNKNOWN" for s in states) else [],
            ),
        )
