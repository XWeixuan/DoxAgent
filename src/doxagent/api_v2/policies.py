"""Shell intersections and period membership over formal Policy lifecycles."""

import json
from decimal import Decimal

from fastapi import Request

from doxagent.v2_read.metrics import decimal_text

from .dto import available, coverage, missing
from .errors import ApiFailure


# Kept as a public query builder for read-contract regression consumers.
from .policy_queries import selection, metrics as query_metrics, page as query_page


def install(app):
    prefix = "/api/doxagent/v2/tickers/{ticker}/policies"
    store, views = app.state.store, app.state.views

    def context(request, ticker, args):
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "POLICIES":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        activation = view["wire"]["activation"]["data"]
        if not activation:
            raise ApiFailure("NO_ACTIVE_REVISION", 404)
        shell = args.get("shell_id")
        if not shell or (
            shell != "ALL"
            and store.get(
                "shell", ticker, activation["document2"]["run_id"] + ":" + shell, view["seq"]
            )
            is None
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
        rows, unknown = query_page(store, ticker, view["seq"], shell, days, selected, after, limit + 1)
        items = []
        for row in rows[:limit]:
            value = json.loads(row["payload"])
            value["matched_filters"] = [
                name
                for name in ("ACTIVE", "ADDED", "HIT", "MODIFIED", "RETIRED", "EXECUTED")
                if row[name] or (name == "ACTIVE" and row["ACTIVE_UNKNOWN"])
            ]
            items.append(value)
        more = len(rows) > limit
        cursor = (
            store.save_token(
                owner,
                scope,
                {"after": rows[limit - 1]["id"], "view_id": args["view_id"], "seq": view["seq"]},
            )
            if more
            else None
        )
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
            resource_coverage=coverage(
                complete=not unknown,
                count=len(items),
                reasons=["POLICY_EFFECTIVENESS_UNKNOWN"] if unknown else [],
            ),
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
        values = query_metrics(store, ticker, view["seq"], shell, days)
        unknown = values.pop("unknown_count")
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
                if value is not None
                else missing("NO_SAMPLES" if state_metric else "NOT_RECORDED"),
                "previous": missing("WINDOW_INCOMPLETE", "NOT_APPLICABLE"),
                "change_pct": missing("WINDOW_INCOMPLETE", "NOT_APPLICABLE"),
                "current_coverage": coverage(complete=state_metric),
                "previous_coverage": None,
                "provisional": False,
            }
            if state_metric and unknown:
                data[name].update(
                    current_coverage=coverage(
                        count=values["active"], reasons=["POLICY_EFFECTIVENESS_UNKNOWN"]
                    ),
                    provisional=True,
                )
        return app.state.respond(request, "PolicyMetrics", data, view_id=args["view_id"])
