"""Maintenance membership and readiness without hydrating unrelated Case evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from doxagent.semantic_clock import boundary

BATCH = 100


def predicate(ticker: str, inputs: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = [
        "c.ticker=?",
        "NOT EXISTS (SELECT 1 FROM runtime_v2_admission_exclusions x "
        "WHERE x.source_message_id=c.source_message_id)",
        "NOT EXISTS (SELECT 1 FROM runtime_values v WHERE v.namespace='invalid_admissions' "
        "AND v.key=c.source_message_id AND v.payload!='false')",
    ]
    args: list[Any] = [ticker]
    if inputs.get("case_ids") is not None:
        # A frozen compensation set wins over day/sweep membership, including an empty set.
        ids = inputs["case_ids"]
        clauses.append("c.case_id IN (SELECT value FROM json_each(?))")
        import json

        args.append(json.dumps(ids))
    elif inputs.get("scope") == "SWEEP":
        clauses.append("json_extract(c.payload_json,'$.sweep_id')=?")
        args.append(inputs["sweep_id"])
    else:
        clauses += ["c.trading_date=?", "json_extract(c.payload_json,'$.sweep_id') IS NULL"]
        args.append(inputs["day"])
    return " AND ".join(clauses), args


def members(
    repository: Any, journal: Any, task: dict[str, Any], *, freeze: bool = True
) -> list[dict[str, Any]]:
    saved = journal.get("maintenance_selection", task["id"]) if freeze else None
    if saved is not None:
        return saved
    selected: list[dict[str, Any]] = []
    captured_at = datetime.now(UTC).isoformat()
    if hasattr(repository, "path"):
        where, args = predicate(task["ticker"], task["inputs"])
        # Scalar snapshot fixes membership/versions, not bodies. Each fetch is bounded.
        with repository._connect() as db:
            db.execute("BEGIN")
            after = ""
            while True:
                rows = db.execute(
                    "SELECT c.case_id,c.source_message_id,c.trading_date,c.updated_at "
                    "FROM runtime_v2_cases c WHERE "
                    + where
                    + " AND c.case_id>? ORDER BY c.case_id LIMIT ?",
                    (*args, after, BATCH),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    item = dict(row)
                    item["record_cutoff"] = captured_at
                    item["effects"] = [
                        tuple(e)
                        for e in db.execute(
                            "SELECT effect_id,attempt_count,status FROM runtime_v2_effects "
                            "WHERE case_id=? ORDER BY effect_id",
                            (row["case_id"],),
                        )
                    ]
                    selected.append(item)
                after = rows[-1]["case_id"]
    else:
        for c in repository.list_cases(task["ticker"]):
            i = task["inputs"]
            match = (
                c.case_id in i["case_ids"]
                if i.get("case_ids") is not None
                else (
                    c.sweep_id == i.get("sweep_id")
                    if i.get("scope") == "SWEEP"
                    else c.trading_date.isoformat() == i["day"] and c.sweep_id is None
                )
            )
            if match and not journal.get("invalid_admissions", c.source.source_message_id):
                selected.append(
                    {
                        "case_id": c.case_id,
                        "source_message_id": c.source.source_message_id,
                        "trading_date": c.trading_date.isoformat(),
                        "updated_at": c.updated_at.isoformat(),
                        "effects": [
                            (e.effect_id, e.attempt_count, e.status.value)
                            for e in repository.list_effects(c.case_id)
                        ],
                    }
                )
    if freeze:
        journal.set("maintenance_selection", task["id"], selected)
    return selected


def unfinished(repository: Any, journal: Any, task: dict[str, Any]) -> bool:
    i, ticker = task["inputs"], task["ticker"]
    if hasattr(repository, "path"):
        where, args = predicate(ticker, i)
        with repository._connect() as db:
            if db.execute(
                "SELECT 1 FROM runtime_v2_cases c WHERE "
                + where
                + " AND EXISTS (SELECT 1 FROM runtime_v2_effects e WHERE e.case_id=c.case_id "
                "AND e.status NOT IN ('COMPLETED','FAILED')) LIMIT 1",
                args,
            ).fetchone():
                return True
            task_where = [
                "t.ticker=?",
                "t.kind='CASE'",
                "t.status IN ('PENDING','RUNNING')",
                "NOT EXISTS (SELECT 1 FROM runtime_values v WHERE v.namespace='invalid_admissions' "
                "AND v.key=substr(t.id,length('inbox:'||t.ticker||':')+1) AND v.payload!='false')",
                "NOT EXISTS (SELECT 1 FROM runtime_v2_admission_exclusions x WHERE "
                "x.source_message_id=substr(t.id,length('inbox:'||t.ticker||':')+1))",
            ]
            task_args: list[Any] = [ticker]
            if i.get("case_ids") is not None:
                task_where.append(
                    "EXISTS (SELECT 1 FROM runtime_v2_cases c WHERE "
                    + where
                    + " AND c.source_message_id=substr(t.id,length('inbox:'||t.ticker||':')+1))"
                )
                task_args += args
            elif i.get("scope") == "SWEEP":
                task_where.append("json_extract(t.inputs,'$.sweep_id')=?")
                task_args.append(i["sweep_id"])
            else:
                task_where += [
                    "json_extract(t.inputs,'$.sweep_id') IS NULL",
                    "julianday(json_extract(t.inputs,'$.admitted_at'))>=julianday(?)",
                    "julianday(json_extract(t.inputs,'$.admitted_at'))<julianday(?)",
                ]
                task_args += [boundary(date.fromisoformat(i["day"])).isoformat(), i["cutoff"]]
            return bool(
                db.execute(
                    "SELECT 1 FROM runtime_tasks t WHERE " + " AND ".join(task_where) + " LIMIT 1",
                    task_args,
                ).fetchone()
            )
    # In-memory test backend has no native files or historical hydration.
    selected = members(repository, journal, task, freeze=False)
    if any(e[2] not in {"COMPLETED", "FAILED"} for c in selected for e in c["effects"]):
        return True
    sources = {c["source_message_id"] for c in selected}
    for t in journal.tasks(ticker=ticker, kind="CASE", active_only=True):
        source_id = t["inputs"].get("source", {}).get("source_message_id")
        if journal.get("invalid_admissions", source_id):
            continue
        if i.get("case_ids") is not None:
            match = source_id in sources
        elif i.get("scope") == "SWEEP":
            match = t["inputs"].get("sweep_id") == i["sweep_id"]
        else:
            at = datetime.fromisoformat(t["inputs"]["admitted_at"])
            match = t["inputs"].get("sweep_id") is None and (
                boundary(date.fromisoformat(i["day"])) <= at < datetime.fromisoformat(i["cutoff"])
            )
        if match:
            return True
    return False


def records(
    repository: Any, selected: list[dict[str, Any]], *, ticker: str
) -> dict[str, list[dict[str, Any]]]:
    from .schema import BadcaseRecord, ProvisionalFactDetail, TradeRecord, W3CoverageGapRecord

    specs = [
        ("candidates", "runtime_v2_candidates", "candidate_identity", ProvisionalFactDetail),
        ("trades", "runtime_v2_trade_records", "case_id", TradeRecord),
        ("badcases", "runtime_v2_badcases", "case_id", BadcaseRecord),
        ("gaps", "runtime_v2_w3_coverage_gaps", "case_id", W3CoverageGapRecord),
    ]
    result: dict[str, list[dict[str, Any]]] = {s[0]: [] for s in specs}
    if hasattr(repository, "path"):
        for offset in range(0, len(selected), BATCH):
            batch = selected[offset : offset + BATCH]
            for name, table, key, model in specs:
                column = "source_message_id" if name == "candidates" else "case_id"
                ids = [c[column] for c in batch]
                after = ""
                while True:
                    with repository._connect() as db:
                        rows = db.execute(
                            "SELECT "
                            + key
                            + ",payload_json FROM "
                            + table
                            + " WHERE daily_status='PENDING' AND "
                            + column
                            + " IN ("
                            + ",".join("?" for _ in ids)
                            + ") AND "
                            + key
                            + ">? AND julianday(created_at)<=julianday(?) ORDER BY "
                            + key
                            + " LIMIT ?",
                            (*ids, after, batch[0]["record_cutoff"], BATCH),
                        ).fetchall()
                    if not rows:
                        break
                    result[name].extend(
                        model.model_validate_json(r["payload_json"]).model_dump(mode="json")
                        for r in rows
                    )
                    after = rows[-1][key]
        return result
    sources = {c["source_message_id"] for c in selected}
    case_ids = {c["case_id"] for c in selected}
    for day in sorted({c["trading_date"] for c in selected}):
        for name, method in [
            ("candidates", "list_daily_candidates"),
            ("trades", "list_daily_trades"),
            ("badcases", "list_daily_badcases"),
            ("gaps", "list_daily_w3_coverage_gaps"),
        ]:
            for item in getattr(repository, method)(ticker, date.fromisoformat(day)):
                if (
                    item.source_message_id in sources
                    if name == "candidates"
                    else item.case_id in case_ids
                ):
                    result[name].append(item.model_dump(mode="json"))
    return result


def case_values(journal: Any, namespace: str, case_ids: list[str]):
    """Read only matching value records, with bounded payload hydration."""
    import json

    for offset in range(0, len(case_ids), BATCH):
        ids = case_ids[offset : offset + BATCH]
        after = ""
        while True:
            with journal.transaction(write=False) as db:
                rows = db.execute(
                    "SELECT key,payload FROM runtime_values WHERE namespace=? AND "
                    "json_extract(payload,'$.case_id') IN ("
                    + ",".join("?" for _ in ids)
                    + ") AND key>? ORDER BY key LIMIT ?",
                    (namespace, *ids, after, BATCH),
                ).fetchall()
            if not rows:
                break
            yield from (json.loads(row["payload"]) for row in rows)
            after = rows[-1]["key"]
