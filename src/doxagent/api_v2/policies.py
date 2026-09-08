"""Shell intersections and period membership over formal Policy lifecycles."""

import json
from decimal import Decimal

from fastapi import Request

from doxagent.v2_read.metrics import decimal_text

from .dto import available, coverage, missing
from .errors import ApiFailure


def selection(ticker, seq, shell, days):
    validity = "valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
    period = " AND day IN (SELECT value FROM json_each(?))" if days is not None else ""
    period_args = [json.dumps(days)] if days is not None else []
    clauses, args = [], []
    for label, kind in (("ADDED", "ADD"), ("MODIFIED", "MODIFY"), ("RETIRED", "RETIRE")):
        clauses.append(
            "EXISTS (SELECT 1 FROM objects ch WHERE ch.kind='policy_change' "
            "AND ch.ticker=p.ticker AND ch.route=p.id AND (?='ALL' OR ch.parent=?) AND "
            + validity
            + period
            + " AND json_extract(ch.payload,'$.type')=?) AS "
            + label
        )
        args.extend([shell, shell, seq, seq, *period_args, kind])
    for label, metric in (("HIT", "policy_ar_hits"), ("EXECUTED", "policy_executed")):
        clauses.append(
            "EXISTS (SELECT 1 FROM contributions c WHERE c.ticker=p.ticker "
            "AND c.metric=? AND json_extract(c.dimensions,'$.business_key')=p.id "
            "AND CAST(c.value AS NUMERIC)>0 AND " + validity + period + ") AS " + label
        )
        args.extend([metric, seq, seq, *period_args])
    sql = (
        "SELECT p.id,p.payload,"
        + ",".join(clauses)
        + (
            ",json_extract(p.payload,'$.lifecycle')='ACTIVE' AND json_extract(p.payload,'$.effective.value')=1 AND json_extract(p.payload,'$.consumed.value')=0 AS ACTIVE FROM objects p "
            "WHERE p.kind='policy_catalog' AND p.ticker=? AND "
            + validity
            + " AND (?='ALL' OR EXISTS (SELECT 1 FROM json_each(p.payload,'$.shell_ids') WHERE value=?))"
        )
    )
    return sql, [*args, ticker, seq, seq, shell, shell]


def install(app):
    prefix = "/api/doxagent/v2/tickers/{ticker}/policies"
    store, views = app.state.store, app.state.views

    def uncertain(sql, parameters):
        with store.connect() as db:
            return db.execute(
                "SELECT count(*) FROM (" + sql + ") WHERE "
                "json_extract(payload,'$.lifecycle')='ACTIVE' AND "
                "(json_extract(payload,'$.effective.value') IS NULL OR "
                "json_extract(payload,'$.consumed.value') IS NULL)", parameters,
            ).fetchone()[0]

    def context(request, ticker, args):
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "POLICIES":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        activation = view["wire"]["activation"]["data"]
        if not activation:
            raise ApiFailure("NO_ACTIVE_REVISION", 404)
        shell = args.get("shell_id")
        if (
            not shell
            or (shell != "ALL" and store.get(
                "shell", ticker, activation["document2"]["run_id"] + ":" + shell, view["seq"]
            )
            is None)
        ):
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        period = view["wire"]["period"]
        days = None if period["selected"] == "ALL" else period["current"]["trading_days"]
        return view, shell, days

    @app.get(prefix)
    async def policies(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "shell_id", "filter", "limit", "cursor"})
        view, shell, days = context(request, ticker, args)
        selected = args.get("filter", "ACTIVE")
        if selected not in {"ACTIVE", "ADDED", "HIT", "MODIFIED", "RETIRED", "EXECUTED"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        try:
            limit = int(args.get("limit", 20))
            if not 1 <= limit <= 100:
                raise ValueError()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        scope = f"policies:{ticker}:{args['view_id']}:{shell}:{selected}:{limit}"
        owner, after = request.state.principal.user_id, ""
        if args.get("cursor"):
            try:
                after = store.token(args["cursor"], owner, scope=scope)["after"]
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
        sql, parameters = selection(ticker, view["seq"], shell, days)
        unknown = uncertain(sql, parameters) if selected == "ACTIVE" else 0
        with store.connect() as db:
            rows = db.execute(
                "SELECT * FROM (" + sql + ") WHERE " + selected + " AND id>? ORDER BY id LIMIT ?",
                [*parameters, after, limit + 1],
            ).fetchall()
        items = []
        for row in rows[:limit]:
            value = json.loads(row["payload"])
            value["matched_filters"] = [
                name
                for name in ("ACTIVE", "ADDED", "HIT", "MODIFIED", "RETIRED", "EXECUTED")
                if row[name]
            ]
            items.append(value)
        more = len(rows) > limit
        cursor = store.save_token(owner, scope, {"after": rows[limit - 1]["id"]}) if more else None
        return app.state.respond(
            request,
            "PolicySummaryPage",
            {
                "items": items,
                "has_more": more,
                "next_cursor": cursor,
                "limit": limit,
                "snapshot_id": args["view_id"],
            },
            view_id=args["view_id"],
            resource_coverage=coverage(complete=not unknown, count=len(items),
                reasons=["POLICY_EFFECTIVENESS_UNKNOWN"] if unknown else []),
        )

    @app.get(prefix + "/changes")
    async def changes(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "shell_id", "policy_id", "limit", "cursor"})
        view, shell, days = context(request, ticker, args)
        data = views.page(
            request.state.principal.user_id,
            "policy_change",
            ticker,
            view=view,
            view_id=args["view_id"],
            parent=None if shell == "ALL" else shell,
            days=days,
            source_id=args.get("policy_id"),
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        return app.state.respond(request, "ChangeEventPage", data, view_id=args["view_id"])

    @app.get(prefix + "/metrics")
    async def metrics(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "shell_id"})
        view, shell, days = context(request, ticker, args)
        sql, parameters = selection(ticker, view["seq"], shell, days)
        with store.connect() as db:
            result = db.execute(
                "SELECT coalesce(sum(ACTIVE),0) AS active, "
                "coalesce(sum(ACTIVE AND json_extract(payload,'$.decision')='LONG'),0) "
                "AS long_active, "
                "coalesce(sum(ACTIVE AND json_extract(payload,'$.decision')='SHORT'),0) "
                "AS short_active, "
                "coalesce(sum(ADDED),0) AS added, coalesce(sum(HIT),0) AS hit, "
                "coalesce(sum(MODIFIED),0) AS modified, coalesce(sum(RETIRED),0) AS retired, "
                "coalesce(sum(EXECUTED),0) AS executed FROM (" + sql + ")",
                parameters,
            ).fetchone()
        values = dict(result)
        unknown = uncertain(sql, parameters)
        values.update(
            long_ratio=Decimal(values["long_active"]) / values["active"]
            if values["active"]
            else None,
            short_ratio=Decimal(values["short_active"]) / values["active"]
            if values["active"]
            else None,
        )
        data = {}
        for name, value in values.items():
            state_metric = name in {
                "active",
                "long_active",
                "short_active",
                "long_ratio",
                "short_ratio",
            }
            data[name] = {
                "metric_id": "policy_" + name,
                "unit": "RATIO" if name.endswith("ratio") else "COUNT",
                "current": available(decimal_text(Decimal(value)))
                if value is not None and (state_metric or value)
                else missing("NO_SAMPLES" if state_metric else "NOT_RECORDED"),
                "previous": missing("WINDOW_INCOMPLETE", "NOT_APPLICABLE"),
                "change_pct": missing("WINDOW_INCOMPLETE", "NOT_APPLICABLE"),
                "current_coverage": coverage(complete=state_metric),
                "previous_coverage": None,
                "provisional": False,
            }
            if state_metric and unknown:
                data[name].update(current=missing("POLICY_EFFECTIVENESS_UNKNOWN", "UNAVAILABLE"),
                    current_coverage=coverage(count=0, reasons=["POLICY_EFFECTIVENESS_UNKNOWN"]))
        return app.state.respond(request, "PolicyMetrics", data, view_id=args["view_id"])
