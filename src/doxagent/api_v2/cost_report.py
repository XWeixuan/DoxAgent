"""Single bounded bucket read for all cost widgets; no per-ticker fan-out."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import Request

from doxagent.semantic_clock import boundary
from doxagent.v2_read.pricing import VERSION
from doxagent.v2_read.repository import instant

from .dto import available, coverage, missing
from .errors import ApiFailure

EXTRA = (
    "cache_write_input_tokens",
    "cache_write_5m_input_tokens",
    "cache_write_1h_input_tokens",
    "cache_write_cost_usd",
)


def source_reasons(view, scope, days):
    reasons = []
    requirements = (
        {"runtime": {"runtime_v2_turns"}, "initialization": {"v2_model_invocations"}}
        if scope == "API"
        else {"runtime": {"runtime_values"}, "initialization": {"initialization_nodes"}}
    )
    proofs = view.get("capture_proofs", {})
    for source, tables in requirements.items():
        proof = proofs.get(source) or {}
        captured = json.loads(proof.get("tables_json", "[]"))
        if not tables <= set(captured) or not (proof.get("complete") or proof.get("closed_end_at")):
            reasons.append("SOURCE_GAP")
            continue
        if days:
            start = boundary(date.fromisoformat(min(days)))
            end = min(
                boundary(date.fromisoformat(max(days)) + timedelta(days=1)),
                datetime.fromisoformat(view["as_of"]),
            )
            # A healthy live window is observed through the frozen source watermark;
            # the unelapsed tail is provisional, not a capture gap.
            proof_end = datetime.fromisoformat(proof.get("closed_end_at") or proof["end_at"])
            if (
                proof.get("complete")
                and (datetime.fromisoformat(view["as_of"]) - proof_end).total_seconds() < 90
            ):
                end = min(end, proof_end)
            if (
                datetime.fromisoformat(proof["started_at"]) > start
                or datetime.fromisoformat(proof.get("closed_end_at") or proof["end_at"]) < end
            ):
                reasons.append("SOURCE_GAP")
    health = view.get("cost_source_health", {})
    for source in requirements:
        state = health.get(source)
        if not state:
            reasons.append("SOURCE_GAP")
        elif (
            state.get("error")
            or state.get("head") is None
            or state.get("checkpoint") is None
            or state.get("head") != state.get("checkpoint")
        ):
            reasons.append("PROJECTION_LAG")
    return sorted(set(reasons))


def total(costs, view, args, sums):
    base = costs.totals(None, view, args, sums=sums, validate_wire=False)
    attempts = int(sums.get("requests", 0))
    known = int(sums.get("total_tokens_samples", 0))
    priced = int(sums.get("total_cost_usd_samples", 0))
    normalized = int(sums.get("normalization_v1_attempts", 0))
    reasons = view.get("cost_source_reasons", [])

    def field(name, unit):
        n = int(sums.get(name + "_samples", 0))
        value = sums.get(name)
        current = (
            available(format(value or Decimal(0), "f"))
            if n or not attempts and not reasons
            else missing()
        )
        if unit == "USD" and args["scope"] == "CODEX":
            current = missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE")
        return {
            "metric_id": name,
            "unit": unit,
            "current": current,
            "previous": missing("NOT_APPLICABLE", "NOT_APPLICABLE"),
            "change_pct": missing("NOT_APPLICABLE", "NOT_APPLICABLE"),
            "previous_coverage": None,
            "current_coverage": coverage(
                complete=n == attempts and not reasons,
                count=n,
                at=view["as_of"],
                reasons=reasons + (["NOT_RECORDED"] if n < attempts else []),
            ),
            "provisional": view["wire"]["period"]["selected"] == "CURRENT_TRADING_DAY",
        }

    if not attempts and not reasons:
        for metric in base.values():
            if isinstance(metric, dict) and "current" in metric and metric["unit"] != "RATIO":
                if args["scope"] != "CODEX" or metric["unit"] != "USD":
                    metric["current"] = available("0")
    # Only paired known samples enter both sides of the cache ratio.
    n = int(sums.get("cache_ratio_input_tokens_samples", 0))
    denominator = sums.get("cache_ratio_input_tokens", Decimal(0))
    applicable = attempts - int(sums.get("cache_inapplicable_attempts", 0))
    ratio = field("cache_hit_ratio", "RATIO")
    ratio["current"] = (
        available(format(sums.get("cache_ratio_read_tokens", Decimal(0)) / denominator, "f"))
        if denominator
        else missing("NO_SAMPLES")
    )
    ratio["current_coverage"] = coverage(
        complete=n == applicable and not reasons,
        count=n,
        at=view["as_of"],
        reasons=reasons + (["NOT_RECORDED"] if n < applicable else []),
    )
    if attempts and not applicable:
        ratio["current"] = missing("NOT_APPLICABLE", "NOT_APPLICABLE")
    base.update(
        cache_hit_ratio=ratio,
        observed_attempts=attempts,
        usage_known_attempts=known,
        usage_unknown_attempts=attempts - known,
        priced_attempts=priced,
        unpriced_attempts=attempts - priced,
        reported_cost_attempts=int(sums.get("reported_cost_attempts", 0)),
        estimated_cost_attempts=int(sums.get("estimated_cost_attempts", 0)),
        unknown_model_price_attempts=int(sums.get("unknown_model_price_attempts", 0)),
        normalized_attempts=normalized,
        cache_ratio_known_attempts=n,
        average_total_tokens_per_request=available(
            format(sums.get("total_tokens", Decimal(0)) / known, "f")
        )
        if known
        else missing("NO_SAMPLES"),
    )
    if sums.get("unknown_model_price_attempts"):
        base["total_cost_usd"]["current_coverage"]["reasons"] = sorted(
            set(base["total_cost_usd"]["current_coverage"]["reasons"] + ["UNKNOWN_MODEL_PRICE"])
        )
    for name in EXTRA:
        base[name] = field(name, "USD" if name.endswith("usd") else "TOKENS")
    for name in ("cost_native_cny", "cost_native_usd"):
        base[name] = (
            available(format(sums.get(name, Decimal(0)), "f"))
            if name in sums or sums.get(name + "_samples")
            else missing()
        )
    if normalized < attempts:
        base["coverage"] = coverage(
            count=known, at=view["as_of"], reasons=sorted(set(reasons + ["HISTORY_INSUFFICIENT"]))
        )
    else:
        base["coverage"] = coverage(
            complete=known == attempts and not reasons,
            count=known,
            at=view["as_of"],
            reasons=reasons + (["NOT_RECORDED"] if known < attempts else []),
        )
    return base


def row_totals(costs, view, args, sums):
    """A row has no comparison period; don't serialize/validate empty comparisons."""
    full = total(costs, view, args, sums)
    counters = (
        "observed_attempts",
        "usage_known_attempts",
        "reported_cost_attempts",
        "estimated_cost_attempts",
        "unpriced_attempts",
        "cache_ratio_known_attempts",
        "cost_native_cny",
        "cost_native_usd",
    )
    metrics = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "cache_hit_ratio",
        "input_cost_usd",
        "output_cost_usd",
        "total_cost_usd",
        *EXTRA,
    )
    return {
        **{name: full[name] for name in counters},
        **{
            name: {key: full[name][key] for key in ("current", "current_coverage", "provisional")}
            for name in metrics
        },
    }


def report(costs, view, args):
    if view["wire"]["page"] != "COST":
        raise ApiFailure("SCOPE_MISMATCH", 400)
    scope = args.get("scope")
    if scope not in {"API", "CODEX"} or args.get("model_id") and not args.get("provider"):
        raise ApiFailure("VALIDATION_FAILED", 422)
    try:
        offset, limit = int(args.get("offset", 0)), int(args.get("limit", 25))
    except ValueError:
        raise ApiFailure("VALIDATION_FAILED", 422) from None
    if offset < 0 or not 1 <= limit <= 50 or args.get("dimension", "NODE") not in {"NODE", "MODEL"}:
        raise ApiFailure("VALIDATION_FAILED", 422)
    period = view["wire"]["period"]
    days = None if period["selected"] == "ALL" else period["current"]["trading_days"]
    params = [view["seq"], view["seq"], scope]
    where = [
        "valid_from<=?",
        "(valid_to IS NULL OR valid_to>?)",
        "scope=?",
        "scope IN ('API','CODEX')",
        "day!='*'",
    ]
    if days is not None:
        where.append("day IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(days))
    bound_ticker = view["wire"].get("ticker")
    selected_ticker = args.get("ticker_selector", "ALL")
    if bound_ticker:
        if selected_ticker not in {"ALL", bound_ticker}:
            raise ApiFailure("SCOPE_MISMATCH", 400)
        where.append("ticker=?")
        params.append(bound_ticker)
    sums, grouped, daily = {}, {}, {}
    tickers, nodes, models, observed_days = set(), set(), set(), set()
    facets = {}
    with costs.store.connect() as db:
        for ticker, day, metric, value, dimensions in db.execute(
            "SELECT ticker,day,metric,value,dimensions FROM "
            + costs.store.metric_table(view["seq"])
            + " WHERE "
            + " AND ".join(where),
            params,
        ):
            dims = facets.get(dimensions)
            if dims is None:
                dims = json.loads(dimensions)
                facets[dimensions] = dims
            node, rnd = dims.get("node", "unknown"), dims.get("round", "UNKNOWN_ROUND")
            leaf = node + rnd if node in {"W1", "W2"} and rnd in {"R1", "R2", "R3"} else node
            filters = {
                "ticker_selector": selected_ticker == "ALL" or selected_ticker == ticker,
                "node_id": not args.get("node_id") or args["node_id"] in {node, leaf},
                "round": not args.get("round") or args["round"] == rnd,
                "provider": not args.get("provider") or args["provider"] == dims.get("provider"),
                "model_id": not args.get("model_id") or args["model_id"] == dims.get("model"),
            }
            if metric == "requests":
                if all(v for k, v in filters.items() if k != "ticker_selector"):
                    tickers.add(ticker)
                if all(v for k, v in filters.items() if k not in {"node_id", "round"}):
                    nodes.add(leaf)
                if all(v for k, v in filters.items() if k not in {"model_id", "provider"}):
                    models.add((dims.get("provider", "unknown"), dims.get("model", "unknown")))
            if not all(filters.values()):
                continue
            observed_days.add(day)
            key = (node, rnd, dims.get("provider", "unknown"), dims.get("model", "unknown"))
            amount = Decimal(value)
            for target in (sums, grouped.setdefault(key, {}), daily.setdefault(day, {})):
                target[metric] = target.get(metric, Decimal(0)) + amount
    reasons = source_reasons(view, scope, days if days is not None else sorted(observed_days))
    view = {**view, "cost_source_reasons": reasons}
    totals = total(costs, view, args, sums)
    rows = [
        {
            "key": json.dumps(key),
            "node": key[0],
            "round": key[1],
            "provider": key[2],
            "model": key[3],
            "totals": row_totals(costs, view, args, values),
        }
        for key, values in sorted(grouped.items())
    ]
    # Shares and trends use the same filtered facts as the summary and row page.
    shares = defaultdict(Decimal)
    unit_metric = "total_cost_usd" if scope == "API" else "total_tokens"
    for key, values in grouped.items():
        label = key[0] + key[1] if key[0] in {"W1", "W2"} and key[1] != "UNKNOWN_ROUND" else key[0]
        if args.get("dimension") == "MODEL":
            label = key[2] + " · " + key[3]
        shares[label] += values.get(unit_metric, Decimal(0))
    denominator = sum(shares.values(), Decimal(0))
    points = []
    days_sorted = sorted(daily)
    bucket_name = "SEMANTIC_DAY"
    calendar_groups = {day: [day] for day in days_sorted}
    if len(calendar_groups) > 60:
        bucket_name = "SEMANTIC_WEEK"
        calendar_groups = {}
        for day in days_sorted:
            at = date.fromisoformat(day)
            calendar_groups.setdefault(str(at - timedelta(days=at.weekday())), []).append(day)
    if len(calendar_groups) > 60:
        bucket_name = "SEMANTIC_MONTH"
        calendar_groups = {}
        for day in days_sorted:
            calendar_groups.setdefault(day[:7], []).append(day)
    calendar_entries = list(calendar_groups.values())
    stride = max(1, (len(calendar_entries) + 59) // 60)
    for i in range(0, len(calendar_entries), stride):
        selected = [day for group in calendar_entries[i : i + stride] for day in group]
        values = {}
        for day in selected:
            for key, value in daily[day].items():
                values[key] = values.get(key, Decimal(0)) + value
        points.append(
            {
                "start_at": instant(boundary(date.fromisoformat(selected[0]))),
                "end_at": instant(boundary(date.fromisoformat(selected[-1]) + timedelta(days=1))),
                "total_tokens": available(format(values.get("total_tokens", Decimal(0)), "f"))
                if values.get("total_tokens_samples")
                else missing(),
                "cost_usd": available(format(values.get("total_cost_usd", Decimal(0)), "f"))
                if values.get("total_cost_usd_samples")
                else missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE")
                if scope == "CODEX"
                else missing(),
                "coverage": total(costs, view, args, values)["coverage"],
            }
        )
    return {
        "scope": scope,
        "pricing_version": VERSION,
        "usd_cny": "6.8",
        "observed_through": view["as_of"],
        "totals": totals,
        "trend": {"scope": scope, "bucket": bucket_name, "points": points},
        "breakdown": {
            "scope": scope,
            "unit": "USD" if scope == "API" else "TOKENS",
            "dimension": args.get("dimension", "NODE"),
            "items": [
                {
                    "key": key,
                    "label": key,
                    "value": format(value, "f"),
                    "ratio": format(value / denominator, "f"),
                }
                for key, value in sorted(shares.items(), key=lambda x: x[1], reverse=True)
                if denominator and value
            ],
            "other": None,
            "coverage": totals["total_cost_usd" if scope == "API" else "total_tokens"][
                "current_coverage"
            ],
        },
        "rows": rows[offset : offset + limit],
        "row_count": len(rows),
        "offset": offset,
        "limit": limit,
        "filter_choices": {
            "tickers": sorted(tickers),
            "nodes": sorted(nodes),
            "models": [{"provider": p, "model_id": m} for p, m in sorted(models)],
        },
        "source_coverage": coverage(complete=not reasons, at=view["as_of"], reasons=reasons),
        "coverage": totals["coverage"],
    }


def install(app, costs):
    @app.get("/api/doxagent/v2/audit/cost/report")
    async def get_report(request: Request):
        args = app.state.query(
            request,
            {
                "view_id",
                "scope",
                "ticker_selector",
                "node_id",
                "round",
                "provider",
                "model_id",
                "dimension",
                "offset",
                "limit",
            },
        )
        view = costs.views.get(request.state.principal.user_id, args.get("view_id", ""))
        data = report(costs, view, args)
        return app.state.respond(request, "CostReport", data, view_id=args["view_id"])
