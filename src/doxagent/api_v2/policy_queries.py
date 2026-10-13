"""Bounded Policy sets at one MVCC watermark; no per-Policy fact subqueries."""

import json


def _scope(ticker, seq, days=None):
    where = "ticker=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
    args = [ticker, seq, seq]
    if days is not None:
        where += " AND day IN (SELECT value FROM json_each(?))"
        args.append(json.dumps(days))
    return where, args


def _objects(db, ticker, seq, kind, columns, days=None, extra="", extra_args=()):
    where, args = _scope(ticker, seq, days)
    where = "kind=? AND " + where + extra
    args = [kind, *args, *extra_args]
    if db is None:
        return f"SELECT {columns} FROM objects WHERE {where}", args
    highwater = int(db.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])
    sql = f"SELECT {columns} FROM object_current WHERE {where}"
    if seq < highwater:
        sql += (
            f" UNION ALL SELECT {columns} FROM objects INDEXED BY history_recent "
            f"WHERE valid_to>? AND {where}"
        )
        args = [*args, seq, *args]
    return sql, args


def _catalog(ticker, seq, shell, db=None, *, include_payload=True):
    extra, args = (
        ("", [])
        if shell == "ALL"
        else (
            " AND instr(json_extract(payload,'$.shell_ids'),?)>0",
            [json.dumps(shell, ensure_ascii=False)],
        )
    )
    raw, parameters = _objects(
        db, ticker, seq, "policy_catalog", "id,payload", extra=extra, extra_args=args
    )
    return (
        "SELECT id,"
        + ("payload," if include_payload else "")
        + "json_extract(payload,'$.lifecycle')='ACTIVE' AND "
        "json_extract(payload,'$.effective.value')=1 "
        "AND json_extract(payload,'$.consumed.value')=0 AS ACTIVE,"
        "json_extract(payload,'$.lifecycle')='ACTIVE' "
        "AND coalesce(json_extract(payload,'$.effective.value'),1)=1 "
        "AND coalesce(json_extract(payload,'$.consumed.value'),0)=0 "
        "AND (json_extract(payload,'$.effective.value') IS NULL OR "
        "json_extract(payload,'$.consumed.value') IS NULL) AS ACTIVE_UNKNOWN,"
        "(json_extract(payload,'$.decision')='LONG' OR "
        "instr(json_extract(payload,'$.executable_directions'),'\"LONG\"')>0) AS long_direction,"
        "(json_extract(payload,'$.decision')='SHORT' OR "
        "instr(json_extract(payload,'$.executable_directions'),'\"SHORT\"')>0) AS short_direction "
        "FROM (" + raw + ")",
        parameters,
    )


def _positive(db, ticker, seq, days):
    where, args = _scope(ticker, seq, days)
    where += " AND metric IN ('policy_ar_hits','policy_executed') AND CAST(value AS NUMERIC)>0"
    columns = "metric,json_extract(dimensions,'$.business_key') AS business_key"
    if db is None:
        return f"SELECT {columns} FROM contributions WHERE {where}", args
    indexes = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE name IN "
            "('policy_positive_current','policy_positive_history')"
        )
    }
    current = "contributions_current" + (
        " INDEXED BY policy_positive_current" if "policy_positive_current" in indexes else ""
    )
    sql = f"SELECT {columns} FROM {current} WHERE {where}"
    highwater = int(db.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])
    if seq < highwater:
        history = "contributions" + (
            " INDEXED BY policy_positive_history" if "policy_positive_history" in indexes else ""
        )
        sql += (
            f" UNION ALL SELECT {columns} FROM {history} WHERE valid_to IS NOT "
            f"NULL AND valid_to>? AND {where}"
        )
        args = [*args, seq, *args]
    return sql, args


def _sets(ticker, seq, shell, days, *, db=None, active_page=None, include_payload=True):
    """Shared revision-bounded catalog and grouped period fact sets."""
    catalog, params = _catalog(ticker, seq, shell, db, include_payload=include_payload)
    if active_page:
        after, limit = active_page
        catalog = (
            "SELECT * FROM ("
            + catalog
            + ") WHERE (ACTIVE OR ACTIVE_UNKNOWN) AND id>? ORDER BY id LIMIT ?"
        )
        params += [after, limit]
    extra, change_args = ("", []) if shell == "ALL" else (" AND parent=?", [shell])
    changes, args = _objects(
        db,
        ticker,
        seq,
        "policy_change",
        "route,json_extract(payload,'$.type') AS type",
        days,
        extra,
        change_args,
    )
    params += args
    positive, args = _positive(db, ticker, seq, days)
    params += args
    return (
        "WITH catalog AS MATERIALIZED (" + catalog + "),"
        "change_flags AS (SELECT route,max(type='ADD') AS ADDED,"
        "max(type='MODIFY') AS MODIFIED,max(type='RETIRE') AS RETIRED "
        "FROM (" + changes + ") WHERE route IN (SELECT id FROM catalog) GROUP BY route),"
        "trade_flags AS (SELECT business_key,max(metric='policy_ar_hits') AS HIT,"
        "max(metric='policy_executed') AS EXECUTED "
        "FROM (" + positive + ") "
        "WHERE business_key IN (SELECT id FROM catalog) GROUP BY business_key) ",
        params,
    )


def selection(ticker, seq, shell, days, *, db=None, active_page=None, include_payload=True):
    ctes, params = _sets(
        ticker, seq, shell, days, db=db, active_page=active_page, include_payload=include_payload
    )
    # A grouped CTE LEFT JOIN can become N x M scans on deployed SQLite.
    # Fold the three disjoint row shapes once by the exact revision key instead;
    # only catalog rows carry definition/state, and flags are catalog-bounded.
    state_columns = (["payload"] if include_payload else []) + [
        "ACTIVE",
        "ACTIVE_UNKNOWN",
        "long_direction",
        "short_direction",
    ]
    state_names = ",".join(state_columns)
    empty_state = ",".join("NULL" for _ in state_columns)
    flags = ["ADDED", "HIT", "MODIFIED", "RETIRED", "EXECUTED"]
    fold = ",".join(f"max({column}) AS {column}" for column in state_columns)
    fold += "," + ",".join(f"coalesce(max({flag}),0) AS {flag}" for flag in flags)
    return (
        ctes + "SELECT id," + fold + " FROM ("
        "SELECT id," + state_names + ","
        "NULL AS ADDED,NULL AS HIT,NULL AS MODIFIED,NULL AS RETIRED,NULL AS EXECUTED "
        "FROM catalog UNION ALL "
        "SELECT route," + empty_state + ",ADDED,NULL,MODIFIED,RETIRED,NULL "
        "FROM change_flags UNION ALL "
        "SELECT business_key," + empty_state + ",NULL,HIT,NULL,NULL,EXECUTED "
        "FROM trade_flags) GROUP BY id",
        params,
    )


def metrics(store, ticker, seq, shell, days):
    with store.connect() as db:
        ctes, args = _sets(ticker, seq, shell, days, db=db, include_payload=False)
        # Each subquery returns exactly one row. Never join thousands of
        # catalog rows to grouped CTEs, and never carry definitions into metrics.
        state = [
            "coalesce(sum(ACTIVE OR ACTIVE_UNKNOWN),0) AS active",
            "coalesce(sum((ACTIVE OR ACTIVE_UNKNOWN) AND long_direction),0) AS long_active",
            "coalesce(sum((ACTIVE OR ACTIVE_UNKNOWN) AND short_direction),0) AS short_active",
            "coalesce(sum(ACTIVE_UNKNOWN),0) AS unknown_count",
        ]
        changes = [
            f"coalesce(sum({name}),0) AS {name.lower()}"
            for name in ("ADDED", "MODIFIED", "RETIRED")
        ]
        trades = [f"coalesce(sum({name}),0) AS {name.lower()}" for name in ("HIT", "EXECUTED")]
        sql = (
            ctes
            + "SELECT c.*,h.*,t.* FROM (SELECT "
            + ",".join(state)
            + " FROM catalog) c CROSS JOIN (SELECT "
            + ",".join(changes)
            + " FROM change_flags) h CROSS JOIN (SELECT "
            + ",".join(trades)
            + " FROM trade_flags) t"
        )
        return dict(db.execute(sql, args).fetchone())


def page(store, ticker, seq, shell, days, selected, after, limit):
    with store.connect() as db:
        catalog, args = _catalog(ticker, seq, shell, db)
        unknown = (
            db.execute(
                "SELECT coalesce(sum(ACTIVE_UNKNOWN),0) FROM (" + catalog + ")", args
            ).fetchone()[0]
            if selected == "ACTIVE"
            else 0
        )
        sql, args = selection(
            ticker,
            seq,
            shell,
            days,
            db=db,
            active_page=(after, limit) if selected == "ACTIVE" else None,
        )
        if selected != "ACTIVE":
            sql = "SELECT * FROM (" + sql + f") WHERE {selected} AND id>? ORDER BY id LIMIT ?"
            args += [after, limit]
        else:
            sql = "SELECT * FROM (" + sql + ") ORDER BY id"
        return db.execute(sql, args).fetchall(), unknown
