"""Frozen pre-optimization query, used only as a small-fixture equivalence oracle."""

import json


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
            ",json_extract(p.payload,'$.lifecycle')='ACTIVE' AND "
            "json_extract(p.payload,'$.effective.value')=1 AND "
            "json_extract(p.payload,'$.consumed.value')=0 AS ACTIVE,"
            "json_extract(p.payload,'$.lifecycle')='ACTIVE' "
            "AND coalesce(json_extract(p.payload,'$.effective.value'),1)=1 "
            "AND coalesce(json_extract(p.payload,'$.consumed.value'),0)=0 "
            "AND (json_extract(p.payload,'$.effective.value') IS NULL "
            "OR json_extract(p.payload,'$.consumed.value') IS NULL) AS "
            "ACTIVE_UNKNOWN FROM objects p "
            "WHERE p.kind='policy_catalog' AND p.ticker=? AND "
            + validity
            + " AND (?='ALL' OR EXISTS (SELECT 1 FROM "
            "json_each(p.payload,'$.shell_ids') WHERE value=?))"
        )
    )
    return sql, [*args, ticker, seq, seq, shell, shell]
