"""Bounded composition of existing operational reads; no Case or message payload reads."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from fastapi import Request

from doxagent.v2_read.repository import instant

from .bus_dashboard import dashboard_data
from .dto import coverage
from .errors import ApiFailure
from .overview import select_overview_tickers, ticker_overview_data
from .runtime import prime_runtime_metrics, runtime_metrics_data

PAGE_BYTES = 220000  # Leave room for the existing API's Resource/meta envelope (256 KiB).


def row_bytes(row):
    return len(json.dumps(row, ensure_ascii=False).encode("utf-8"))


def install(app):
    store, views = app.state.store, app.state.views

    @app.get("/api/doxagent/v2/overview/operations")
    async def operations(request: Request):
        args = app.state.query(request, {"view_id", "section", "ticker", "limit", "cursor"})
        owner = request.state.principal.user_id
        section = args.get("section", "summary")
        if section not in {"summary", "sources"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        try:
            limit = int(args.get("limit", 20))
            if not 1 <= limit <= 20:
                raise ValueError()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        view_id = args.get("view_id", "")
        view = views.get(owner, view_id, args.get("ticker"))
        if view["wire"]["page"] != ("OVERVIEW" if section == "summary" else "MESSAGE_BUS"):
            raise ApiFailure("SCOPE_MISMATCH", 400)
        scope = f"agent-overview:{view_id}:{section}:{args.get('ticker')}:{limit}"
        after = ""
        if args.get("cursor"):
            try:
                after = store.token(args["cursor"], owner, scope=scope)["after"]
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
        if section == "sources":
            ticker = args.get("ticker")
            if not ticker or view["wire"]["ticker"] != ticker:
                raise ApiFailure("SCOPE_MISMATCH", 400)
            data = dashboard_data(
                store,
                ticker,
                view,
                {},
                source_after=after,
                source_limit=limit,
                include_metrics=False,
            )
            selected = data["sources"]
            items, size = [], 0
            for row in selected[:limit]:
                next_size = size + row_bytes(row)
                if next_size > PAGE_BYTES:
                    if not items:
                        raise ApiFailure("RESOURCE_TOO_LARGE", 413)
                    break
                items.append(row)
                size = next_size
            more = len(selected) > len(items)
            last = items[-1]["source"]["binding_id"] if items else after
            schema = "SourceStatusPage"
        else:
            selected = select_overview_tickers(store, view, after, limit + 1)
            runtime_service = (
                prime_runtime_metrics(
                    store, views.derive(view, "RUNTIME", selected[0]), selected[:limit]
                )
                if selected
                else None
            )
            items, size = [], 0
            from doxagent.v2_read.query_budget import deadline

            until = deadline.get()
            for row in ticker_overview_data(store, view, selected[:limit]):
                # Leave time to serialize and pin the continuation. A slow SQL query
                # still obeys the existing worker deadline; no background fallback.
                if items and until is not None and time.monotonic() + 0.6 >= until:
                    break
                ticker = row["state"]["ticker"]
                child = views.derive(view, "RUNTIME", ticker)
                running = not row["state"]["initialization_incomplete"]
                runtime = (
                    runtime_metrics_data(store, ticker, child, runtime_service) if running else None
                )
                bus = (
                    dashboard_data(
                        store,
                        ticker,
                        views.derive(view, "MESSAGE_BUS", ticker),
                        {},
                        include_sources=False,
                    )
                    if running
                    else None
                )

                def resource(value):
                    return {
                        "state": "AVAILABLE" if value is not None else "UNAVAILABLE",
                        "data": value,
                        "reason": None if value is not None else "NOT_PRODUCED",
                        "coverage": coverage(complete=value is not None),
                    }

                item = {
                    "overview": row,
                    "runtime": resource(runtime),
                    "bus": resource(bus),
                    "period": child["wire"]["period"],
                    "bus_read_at": instant(datetime.now(UTC)),
                }
                next_size = size + row_bytes(item)
                if next_size > PAGE_BYTES:
                    if not items:
                        raise ApiFailure("RESOURCE_TOO_LARGE", 413)
                    break
                items.append(item)
                size = next_size
            more = len(selected) > len(items)
            last = items[-1]["overview"]["state"]["ticker"] if items else after
            schema = "OperationsOverviewPage"
        cursor = (
            store.save_token(owner, scope, {"after": last, "view_id": view_id, "seq": view["seq"]})
            if more
            else None
        )
        return app.state.respond(
            request,
            schema,
            {
                "items": items,
                "limit": limit,
                "has_more": more,
                "next_cursor": cursor,
                "snapshot_id": view_id,
            },
            view_id=view_id,
            headers={"X-DoxAgent-Live-Read-At": instant(datetime.now(UTC))},
        )
