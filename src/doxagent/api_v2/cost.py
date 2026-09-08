"""Exact local cost aggregates over observed invocation contributions."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from fastapi import FastAPI, Request

from doxagent.semantic_clock import boundary
from doxagent.v2_read.repository import encode, instant

from .dto import available, coverage, missing, validate
from .errors import ApiFailure

TOKEN_METRICS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
COST_METRICS = (
    "noncached_input_cost_usd",
    "cached_input_cost_usd",
    "input_cost_usd",
    "output_cost_usd",
    "total_cost_usd",
)


class Costs:
    def __init__(self, store, views):
        self.store, self.views = store, views

    def conditions(self, ticker, view, args):
        if args.get("scope") not in {"API", "CODEX"}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        if args.get("model_id") and not args.get("provider"):
            raise ApiFailure("VALIDATION_FAILED", 422)
        if view["wire"]["page"] != "COST":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        where = [
            "ticker=?",
            "valid_from<=?",
            "(valid_to IS NULL OR valid_to>?)",
            "json_extract(dimensions,'$.scope')=?",
        ]
        params = [ticker, view["seq"], view["seq"], args["scope"]]
        for key, field in (("node_id", "node"), ("provider", "provider"), ("model_id", "model")):
            if args.get(key):
                where.append("json_extract(dimensions,'$." + field + "')=?")
                params.append(args[key])
        period = view["wire"]["period"]
        if period["selected"] == "ALL":
            where.append("day='*'")
        else:
            days = period["current"]["trading_days"]
            where.append("day IN (" + ",".join("?" for _ in days) + ")" if days else "0")
            params.extend(days)
        return where, params

    def totals(self, ticker, view, args):
        where, params = self.conditions(ticker, view, args)
        sums = {}
        with self.store.connect() as db:
            for row in db.execute(
                "SELECT metric,value FROM metric_buckets WHERE " + " AND ".join(where), params
            ):
                sums[row[0]] = sums.get(row[0], Decimal(0)) + Decimal(row[1])
        requests = int(sums.get("requests", 0))
        observed = coverage(count=requests)

        def metric(name, unit):
            amount = sums.get(name)
            sample_count = int(sums.get(name + "_samples", 0))
            if amount is None and sample_count:
                amount = Decimal(0)
            current = available(format(amount, "f")) if amount is not None else missing()
            reasons = []
            if args["scope"] == "CODEX" and unit == "USD":
                current = missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE")
            elif sample_count < requests:
                reasons.append("NOT_RECORDED")
            return {
                "metric_id": name,
                "unit": unit,
                "current": current,
                "previous": missing("NOT_APPLICABLE", "NOT_APPLICABLE"),
                "change_pct": missing("NOT_APPLICABLE", "NOT_APPLICABLE"),
                "current_coverage": coverage(count=sample_count, reasons=reasons or ["SOURCE_GAP"]),
                "previous_coverage": None,
                "provisional": False,
            }

        values = {name: metric(name, "TOKENS") for name in TOKEN_METRICS}
        values.update({name: metric(name, "USD") for name in COST_METRICS})
        ratio = metric("cache_hit_ratio", "RATIO")
        incoming, cached = sums.get("input_tokens"), sums.get("cached_input_tokens")
        if cached is None and sums.get("cached_input_tokens_samples"):
            cached = Decimal(0)
        ratio["current"] = (
            available(format(cached / incoming, "f"))
            if incoming
            and cached is not None
            and sums.get("cached_input_tokens_samples") == requests
            and sums.get("input_tokens_samples") == requests
            else missing("NO_SAMPLES" if not incoming else "NOT_RECORDED")
        )
        values.update(
            requests=available(requests),
            cache_hit_ratio=ratio,
            coverage=observed,
            average_total_tokens_per_request=available(
                format(sums.get("total_tokens", Decimal(0)) / requests, "f")
            )
            if requests and sums.get("total_tokens_samples") == requests
            else missing(),
            unpriced_request_count=available(int(sums.get("unpriced_requests", 0)))
            if args["scope"] == "API"
            else missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE"),
        )
        return validate("UsageTotals", values)

    def choices(self, owner, ticker, view_id, view, args, dimension):
        limit = int(args.get("limit", 20))
        if not 1 <= limit <= 100:
            raise ApiFailure("VALIDATION_FAILED", 422)
        scope = hashlib.sha256(
            encode(
                [
                    "cost-choices",
                    ticker,
                    view_id,
                    args["scope"],
                    dimension,
                    limit,
                    [args.get(k) for k in ("node_id", "provider", "model_id")],
                ]
            ).encode()
        ).hexdigest()
        after = ""
        if args.get("cursor"):
            try:
                after = self.store.token(args["cursor"], owner, scope=scope)["after"]
            except TimeoutError:
                raise ApiFailure("CURSOR_EXPIRED", 410) from None
            except ValueError:
                raise ApiFailure("INVALID_CURSOR", 400) from None
        where, params = self.conditions(ticker, view, args)
        expression = (
            "json_extract(dimensions,'$.node')"
            if dimension == "NODE"
            else "json_array(json_extract(dimensions,'$.provider'),"
            "json_extract(dimensions,'$.model'))"
        )
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT "
                + expression
                + " AS item FROM metric_buckets WHERE "
                + " AND ".join(where)
                + " AND metric='requests' AND "
                + expression
                + ">? ORDER BY item LIMIT ?",
                [*params, after, limit + 1],
            ).fetchall()
        more = len(rows) > limit
        selected = rows[:limit]
        items = [
            {"node_id": row[0], "label": row[0]}
            if dimension == "NODE"
            else {"provider": json.loads(row[0])[0], "model_id": json.loads(row[0])[1]}
            for row in selected
        ]
        cursor = self.store.save_token(owner, scope, {"after": selected[-1][0]}) if more else None
        return {
            "items": items,
            "limit": limit,
            "has_more": more,
            "next_cursor": cursor,
            "snapshot_id": view_id,
        }


def install(app: FastAPI):
    costs = Costs(app.state.store, app.state.views)
    prefix = "/api/doxagent/v2/tickers/{ticker}/audit/cost"
    allowed = {"view_id", "scope", "node_id", "provider", "model_id"}

    def view(request, ticker, args):
        return costs.views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)

    @app.get(prefix + "/summary")
    async def summary(ticker: str, request: Request):
        args = app.state.query(request, allowed)
        data = {
            "scope": args.get("scope"),
            "pricing_version": "frontend-fixed-20260907",
            "usd_cny": "6.8",
            "totals": costs.totals(ticker, view(request, ticker, args), args),
        }
        return app.state.respond(request, "CostSummary", data, view_id=args["view_id"])

    @app.get(prefix + "/filters")
    async def filters(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "scope", "dimension", "limit", "cursor"})
        current = view(request, ticker, args)
        costs.conditions(ticker, current, args)
        dimension = args.get("dimension")
        if dimension not in {None, "NODE", "MODEL"} or args.get("cursor") and dimension is None:
            raise ApiFailure("VALIDATION_FAILED", 422)
        data = {"scope": args["scope"]}
        for kind, key in (("NODE", "nodes"), ("MODEL", "models")):
            data[key] = (
                costs.choices(
                    request.state.principal.user_id, ticker, args["view_id"], current, args, kind
                )
                if dimension in {None, kind}
                else {
                    "items": [],
                    "limit": int(args.get("limit", 20)),
                    "has_more": False,
                    "next_cursor": None,
                    "snapshot_id": args["view_id"],
                }
            )
        return app.state.respond(request, "AuditFilters", data, view_id=args["view_id"])

    @app.get(prefix + "/nodes")
    async def nodes(ticker: str, request: Request):
        args = app.state.query(request, allowed | {"limit", "cursor"})
        current = view(request, ticker, args)
        page = costs.choices(
            request.state.principal.user_id, ticker, args["view_id"], current, args, "NODE"
        )
        items = []
        for item in page["items"]:
            selected = {**args, "node_id": item["node_id"]}
            where, params = costs.conditions(ticker, current, selected)
            with costs.store.connect() as db:
                rows = db.execute(
                    "SELECT DISTINCT json_extract(dimensions,'$.provider'),"
                    "json_extract(dimensions,'$.model') FROM metric_buckets WHERE "
                    + " AND ".join(where)
                    + " AND metric='requests' ORDER BY 1,2 LIMIT 101",
                    params,
                ).fetchall()
            if len(rows) > 100:
                raise ApiFailure("RESOURCE_TOO_LARGE", 413)
            items.append(
                {
                    **item,
                    "models": [{"provider": row[0], "model_id": row[1]} for row in rows],
                    "totals": costs.totals(ticker, current, selected),
                }
            )
        page["items"] = items
        return app.state.respond(request, "CostNodeRowPage", page, view_id=args["view_id"])

    @app.get(prefix + "/breakdown")
    async def breakdown(ticker: str, request: Request):
        args = app.state.query(request, allowed | {"dimension", "limit"})
        current = view(request, ticker, args)
        where, params = costs.conditions(ticker, current, args)
        dimension = args.get("dimension")
        limit = int(args.get("limit", 10))
        if dimension not in {"NODE", "MODEL"} or not 1 <= limit <= 100:
            raise ApiFailure("VALIDATION_FAILED", 422)
        metric = "total_cost_usd" if args["scope"] == "API" else "total_tokens"
        groups = {}
        with costs.store.connect() as db:
            for row in db.execute(
                "SELECT dimensions,value FROM metric_buckets WHERE "
                + " AND ".join(where)
                + " AND metric=?",
                [*params, metric],
            ):
                dimensions = json.loads(row[0])
                key = (
                    dimensions["node"]
                    if dimension == "NODE"
                    else encode([dimensions["provider"], dimensions["model"]])
                )
                groups[key] = groups.get(key, Decimal(0)) + Decimal(row[1])
        total = sum(groups.values(), Decimal(0))
        ordered = sorted(groups.items(), key=lambda item: (-item[1], item[0]))

        def share(key, amount):
            return {
                "key": key,
                "label": key,
                "value": format(amount, "f"),
                "ratio": format(amount / total if total else Decimal(0), "f"),
            }

        data = {
            "scope": args["scope"],
            "unit": "USD" if args["scope"] == "API" else "TOKENS",
            "dimension": dimension,
            "items": [share(key, amount) for key, amount in ordered[:limit]],
            "other": share("OTHER", sum((amount for _, amount in ordered[limit:]), Decimal(0)))
            if len(ordered) > limit
            else None,
            "coverage": coverage(count=len(groups)),
        }
        return app.state.respond(request, "CostBreakdown", data, view_id=args["view_id"])

    @app.get(prefix + "/trend")
    async def trend(ticker: str, request: Request):
        args = app.state.query(request, allowed | {"max_points"})
        current = view(request, ticker, args)
        where, params = costs.conditions(ticker, current, args)
        maximum = int(args.get("max_points", 100))
        if not 1 <= maximum <= 200:
            raise ApiFailure("VALIDATION_FAILED", 422)
        if "day='*'" in where:
            where.remove("day='*'")
            where.append("day!='*'")
        days = {}
        with costs.store.connect() as db:
            for row in db.execute(
                "SELECT day,metric,value FROM metric_buckets WHERE "
                + " AND ".join(where)
                + " AND metric IN ('requests','total_tokens','total_tokens_samples',"
                "'total_cost_usd','total_cost_usd_samples') ORDER BY day",
                params,
            ):
                bucket = days.setdefault(row[0], {})
                bucket[row[1]] = bucket.get(row[1], Decimal(0)) + Decimal(row[2])
        bucket_name = "SEMANTIC_DAY"
        keys = sorted(days)

        def group_key(value):
            if bucket_name == "SEMANTIC_WEEK":
                return str(
                    date.fromisoformat(value) - timedelta(days=date.fromisoformat(value).weekday())
                )
            if bucket_name == "SEMANTIC_MONTH":
                return value[:7] + "-01"
            return value

        if len(keys) > maximum:
            bucket_name = "SEMANTIC_WEEK"
        if len({group_key(key) for key in keys}) > maximum:
            bucket_name = "SEMANTIC_MONTH"
        groups = {}
        for day, values in days.items():
            bucket = groups.setdefault(group_key(day), {"last": day, "values": {}})
            bucket["last"] = max(bucket["last"], day)
            for metric, amount in values.items():
                bucket["values"][metric] = bucket["values"].get(metric, Decimal(0)) + amount
        # A point may span several consecutive months for very long ALL histories.
        entries = sorted(groups.items())
        stride = max(1, (len(entries) + maximum - 1) // maximum)
        points = []
        for offset in range(0, len(entries), stride):
            selected = entries[offset : offset + stride]
            merged = {}
            for _, entry in selected:
                for metric, amount in entry["values"].items():
                    merged[metric] = merged.get(metric, Decimal(0)) + amount

            def known(metric, merged=merged):
                return (
                    available(format(merged.get(metric, Decimal(0)), "f"))
                    if merged.get(metric + "_samples")
                    else missing()
                )

            points.append(
                {
                    "start_at": instant(boundary(date.fromisoformat(selected[0][0]))),
                    "end_at": instant(
                        boundary(date.fromisoformat(selected[-1][1]["last"]) + timedelta(days=1))
                    ),
                    "total_tokens": known("total_tokens"),
                    "cost_usd": known("total_cost_usd")
                    if args["scope"] == "API"
                    else missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE"),
                    "coverage": coverage(count=int(merged.get("requests", 0))),
                }
            )
        return app.state.respond(
            request,
            "CostTrend",
            {"scope": args["scope"], "bucket": bucket_name, "points": points},
            view_id=args["view_id"],
        )
