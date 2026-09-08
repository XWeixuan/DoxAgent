"""Event window filters over immutable formal lifecycle facts."""

import json

from fastapi import Request

from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.ordering import occurrence_anchor

from .dto import available, coverage, missing
from .errors import ApiFailure


def selection(ticker, seq, days):
    args, flags = [], []
    for label in ("ADDED", "MODIFIED", "RETIRED"):
        period = " AND ch.day IN (SELECT value FROM json_each(?))" if days is not None else ""
        flags.append(
            "EXISTS (SELECT 1 FROM objects ch WHERE ch.kind='event_change' "
            "AND ch.ticker=e.ticker AND ch.parent=e.id AND ch.route=? "
            "AND ch.valid_from<=? AND (ch.valid_to IS NULL OR ch.valid_to>?)"
            + period
            + ") AS "
            + label
        )
        args.extend([label, seq, seq, *([json.dumps(days)] if days is not None else [])])
    sql = (
        "SELECT e.id,e.payload,occurrence_anchor(json_extract(e.payload,'$.occurred_at'),"
        "json_extract(e.payload,'$.occurrence_time_precision')) "
        "AS occurred_at,e.route='ACTIVE' AS ACTIVE,"
        + ",".join(flags)
        + (
            " FROM objects e WHERE e.kind='event_catalog' AND e.ticker=? "
            "AND e.valid_from<=? AND (e.valid_to IS NULL OR e.valid_to>?)"
        )
    )
    return sql, [*args, ticker, seq, seq]


def install(app):
    prefix = "/api/doxagent/v2/tickers/{ticker}/event-library"
    store, views = app.state.store, app.state.views

    def context(request, ticker, args):
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "EVENTS":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        if not view["wire"]["activation"]["data"]:
            raise ApiFailure("NO_ACTIVE_REVISION", 404)
        period = view["wire"]["period"]
        return view, None if period["selected"] == "ALL" else period["current"]["trading_days"]

    @app.get(prefix + "/events")
    @app.get(prefix + "/index")
    async def events(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "filter", "limit", "cursor"})
        view, days = context(request, ticker, args)
        selected = args.get("filter", "ACTIVE")
        if selected not in {"ACTIVE", "ADDED", "MODIFIED", "RETIRED"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        try:
            limit = int(args.get("limit", 20))
            if not 1 <= limit <= 100:
                raise ValueError()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        scope = f"events:{ticker}:{args['view_id']}:{selected}:{limit}"
        owner, after = request.state.principal.user_id, None
        if args.get("cursor"):
            try:
                after = store.token(args["cursor"], owner, scope=scope)["after"]
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
        sql, params = selection(ticker, view["seq"], days)
        with store.connect() as db:
            db.create_function("occurrence_anchor", 2, occurrence_anchor, deterministic=True)
            rows = db.execute(
                "SELECT * FROM ("
                + sql
                + ") WHERE "
                + selected
                + (" AND (occurred_at,id)<(?,?)" if after else "")
                + " ORDER BY occurred_at DESC,id DESC LIMIT ?",
                [*params, *(after or []), limit + 1],
            ).fetchall()
        items = []
        for row in rows[:limit]:
            item = json.loads(row["payload"])
            item["matched_filters"] = [
                name for name in ("ACTIVE", "ADDED", "MODIFIED", "RETIRED") if row[name]
            ]
            items.append(item)
        more = len(rows) > limit
        cursor = (
            store.save_token(
                owner, scope, {"after": [rows[limit - 1]["occurred_at"], rows[limit - 1]["id"]]}
            )
            if more
            else None
        )
        return app.state.respond(
            request,
            "EventSummaryPage",
            {
                "items": items,
                "has_more": more,
                "next_cursor": cursor,
                "limit": limit,
                "snapshot_id": args["view_id"],
            },
            view_id=args["view_id"],
        )

    @app.get(prefix + "/metrics")
    async def metrics(ticker: str, request: Request):
        args = app.state.query(request, {"view_id"})
        view, days = context(request, ticker, args)
        service = Metrics(store)
        data = {
            name: service.metric(
                name,
                [ticker],
                view["seq"],
                days=days,
                distinct=True,
                previous_days=(view["wire"]["period"].get("previous") or {}).get("trading_days"),
            )
            for name in (
                "added_events",
                "added_facts",
                "modified_events",
                "modified_facts",
                "retired_events",
                "retired_facts",
            )
        }
        with store.connect() as db:
            counts = dict(
                db.execute(
                    "SELECT kind,count(*) FROM objects "
                    "WHERE kind IN ('event_catalog','fact_catalog') "
                    "AND ticker=? AND route='ACTIVE' AND valid_from<=? "
                    "AND (valid_to IS NULL OR valid_to>?) GROUP BY kind",
                    (ticker, view["seq"], view["seq"]),
                )
            )
        for name, kind in (("active_events", "event_catalog"), ("active_facts", "fact_catalog")):
            data[name] = {
                "metric_id": name,
                "unit": "COUNT",
                "current": available(str(counts.get(kind, 0))),
                "previous": missing("WINDOW_INCOMPLETE", "NOT_APPLICABLE"),
                "change_pct": missing("WINDOW_INCOMPLETE", "NOT_APPLICABLE"),
                "current_coverage": coverage(complete=True),
                "previous_coverage": None,
                "provisional": False,
            }
        return app.state.respond(request, "EventMetrics", data, view_id=args["view_id"])
