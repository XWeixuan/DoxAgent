"""Message and completion-attempt cohorts share exact formal message filters."""

import json
from decimal import Decimal

from doxagent.v2_read.metrics import compare, decimal_text

from .dto import available, coverage, missing


def counts(store, ticker, seq, filters, days):
    clauses = [
        "m.kind='message'",
        "m.ticker=?",
        "m.valid_from<=?",
        "(m.valid_to IS NULL OR m.valid_to>?)",
    ]
    args = [ticker, seq, seq]
    for name in ("source_id", "route"):
        if name in filters:
            clauses.append("m." + name + "=?")
            args.append(filters[name])
    if filters.get("source_kind"):
        clauses.append("json_extract(m.payload,'$.source.kind')=?")
        args.append(filters["source_kind"])
    if filters.get("q"):
        clauses.append("instr(m.search_text,?)>0")
        args.append(filters["q"].casefold())
    where = " AND ".join(clauses)
    window = " AND m.day IN (SELECT value FROM json_each(?))" if days is not None else ""
    window_args = [json.dumps(days)] if days is not None else []
    with store.connect() as db:
        messages = db.execute(
            "SELECT count(*) FROM objects m WHERE " + where + window, [*args, *window_args]
        ).fetchone()[0]
        attempts = db.execute(
            "SELECT count(*),coalesce(sum(json_extract(a.payload,'$.succeeded')),0) "
            "FROM objects m JOIN objects l ON l.kind='message_raw_link' AND l.ticker=m.ticker "
            "AND l.parent=m.id AND l.valid_from<=? AND (l.valid_to IS NULL OR l.valid_to>?) "
            "JOIN objects a ON a.kind='body_attempt' AND a.ticker=m.ticker AND a.parent=l.id "
            "AND a.valid_from<=? AND (a.valid_to IS NULL OR a.valid_to>?) WHERE "
            + where
            + (" AND a.day IN (SELECT value FROM json_each(?))" if days is not None else ""),
            [seq, seq, seq, seq, *args, *window_args],
        ).fetchone()
    return Decimal(messages), Decimal(attempts[1]) / attempts[0] if attempts[0] else None


def metrics(store, ticker, view, filters):
    period = view["wire"]["period"]
    days = None if period["selected"] == "ALL" else period["current"]["trading_days"]
    current = counts(store, ticker, view["seq"], filters, days)
    prior_days = period["previous"]["trading_days"] if period["previous"] else None
    previous = (
        counts(store, ticker, view["seq"], filters, prior_days) if prior_days else (None, None)
    )
    return {
        name: {
            "metric_id": name,
            "unit": "COUNT" if i == 0 else "RATIO",
            "current": available(decimal_text(current[i]))
            if current[i] is not None and (i or current[i])
            else missing(),
            "previous": available(decimal_text(previous[i]))
            if previous[i] is not None and (i or previous[i])
            else missing(),
            "change_pct": compare(
                current[i], previous[i], applicable=period["comparison_applicable"]
            ),
            "current_coverage": coverage(),
            "previous_coverage": coverage() if prior_days else None,
            "provisional": False,
        }
        for i, name in enumerate(("published_revisions", "body_completion_success_ratio"))
    }
