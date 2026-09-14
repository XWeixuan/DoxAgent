"""Message and completion-attempt cohorts share exact formal message filters."""

import json
from decimal import Decimal

from doxagent.v2_read.metrics import compare, decimal_text

from .dto import available, coverage, missing


def counts(store, ticker, seq, filters, days):
    if not filters.get("q"):
        with store.connect() as db:
            start = db.execute("SELECT value FROM read_meta WHERE key='bus_aggregates_from'").fetchone()
            if start and seq >= int(start[0]):
                where = ["metric IN ('bus_messages','bus_body_attempts','bus_body_succeeded')", "ticker=?", "valid_from<=?", "(valid_to IS NULL OR valid_to>?)"]
                params = [ticker,seq,seq]
                for field in ("source_id","source_kind","route"):
                    if filters.get(field):
                        where.append(field+"=?")
                        params.append(filters[field])
                if days is None:
                    where.append("day='*'")
                else:
                    where.append("day IN ("+",".join("?" for _ in days)+")" if days else "0")
                    params.extend(days)
                totals = {}
                for metric,value in db.execute("SELECT metric,value FROM " + store.metric_table(seq) + " WHERE "+" AND ".join(where),params):
                    totals[metric] = totals.get(metric,Decimal(0))+Decimal(value)
                attempts = totals.get("bus_body_attempts",Decimal(0))
                return totals.get("bus_messages",Decimal(0)), totals.get("bus_body_succeeded",Decimal(0))/attempts if attempts else None
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
            "SELECT count(*) FROM " + store.snapshot_table(seq) + " m WHERE " + where + window, [*args, *window_args]
        ).fetchone()[0]
        attempts = db.execute(
            "SELECT count(*),coalesce(sum(json_extract(a.payload,'$.succeeded')),0) "
            "FROM " + store.snapshot_table(seq) + " m JOIN " + store.snapshot_table(seq) + " l ON l.kind='message_raw_link' AND l.ticker=m.ticker "
            "AND l.parent=m.id AND l.valid_from<=? AND (l.valid_to IS NULL OR l.valid_to>?) "
            "JOIN " + store.snapshot_table(seq) + " a ON a.kind='body_attempt' AND a.ticker=m.ticker AND a.parent=l.id "
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
