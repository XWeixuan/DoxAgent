"""Lifecycle transitions only when a formal activation becomes effective."""

import json
from datetime import datetime
from hashlib import sha256

from doxagent.api_v2.dto import available, missing, validate
from doxagent.semantic_clock import semantic_day

from .repository import encode


def rows(store, kind, ticker, parent=None):
    # Worker-side enumeration of one immutable version, never an HTTP history scan.
    with store.connect() as db:
        sql = "SELECT id,payload FROM objects WHERE kind=? AND ticker=? AND valid_to IS NULL"
        args = [kind, ticker]
        if parent is not None:
            sql += " AND parent=?"
            args.append(parent)
        import json

        return {row[0]: json.loads(row[1]) for row in db.execute(sql, args)}


def changed_paths(before, after, prefix=""):
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            path
            for key in sorted(set(before) | set(after))
            for path in changed_paths(
                before.get(key),
                after.get(key),
                prefix + "/" + key.replace("~", "~0").replace("/", "~1"),
            )
        ]
    return [] if before == after else [prefix or "/"]


def activate(store, ticker, active, at):
    admitted_at = at
    # Definition lifecycle belongs to formal publication, not import/admission wall time.
    at = active["policy_set"]["published_at"]
    records, metrics = [], []
    prior = store.get("activation", ticker, "active")
    if prior and prior["runtime_activation_id"] == active["runtime_activation_id"]:
        return records, metrics
    version = active["policy_set"]["policy_set_version"]
    details = rows(store, "policy_detail", ticker, active["policy_set"]["artifact_id"])
    incoming = {
        d["summary"]["policy_id"] + ":" + d["summary"]["policy_activation_revision"]: d
        for d in details.values()
    }
    catalog = rows(store, "policy_catalog", ticker)
    previous_by_id = {p["policy_id"]: p for p in catalog.values() if p["lifecycle"] == "ACTIVE"}
    current_ids = {d["summary"]["policy_id"] for d in incoming.values()}
    for key in sorted(set(incoming) | set(catalog)):
        previous = catalog.get(key)
        current = incoming.get(key)
        replaced = (
            previous_by_id.get(current["summary"]["policy_id"])
            if current and not previous
            else None
        )
        if current is None and previous["lifecycle"] == "RETIRED":
            continue
        summary = dict(current["summary"] if current else previous)
        summary["lifecycle"] = "ACTIVE" if current else "RETIRED"
        summary["matched_filters"] = ["ACTIVE"] if current else ["RETIRED"]
        if current and not previous and not store.get("policy_admission", ticker, key):
            # Never substitute publication/import time for first runtime admission.
            # A BACKFILL records an unknown first admission permanently.
            records.append(
                {
                    "kind": "policy_admission",
                    "ticker": ticker,
                    "id": key,
                    "data": {
                        "first_activated_at": admitted_at,
                        "runtime_activation_id": active["runtime_activation_id"],
                    },
                }
            )
        if not current:
            summary["effective"] = available(False)
        consumed = store.get("policy_consumption", ticker, key)
        if consumed:
            summary.update(
                consumed=available(True),
                consumed_at=available(consumed["activated_at"]),
                effective=available(False),
            )
        records.append(
            {
                "kind": "policy_catalog",
                "ticker": ticker,
                "id": key,
                "data": validate("PolicySummary", summary),
            }
        )
        before_summary = previous or replaced
        before_detail = (
            store.get("policy_detail", ticker, before_summary["policy_revision_id"])
            if before_summary
            else None
        )
        before = before_detail["policy"] if before_detail else None
        after = current["policy"] if current else None
        paths = changed_paths(before, after)
        change_type = (
            "ADD"
            if previous is None
            else "RETIRE"
            if current is None
            else ("RESTORE" if previous["lifecycle"] == "RETIRED" else "MODIFY" if paths else None)
        )
        if replaced:
            change_type = "MODIFY"
        if current is None and summary["policy_id"] in current_ids:
            change_type = None  # Superseded activation revision, not retired business Policy.
        if not at or change_type is None:
            continue
        identity = sha256(
            encode([active["runtime_activation_id"], key, change_type]).encode()
        ).hexdigest()
        change = validate(
            "ChangeEvent",
            {
                "change_id": identity,
                "object_id": summary["policy_id"],
                "type": change_type,
                "occurred_at": at,
                "title": summary["title"],
                "from_version": prior["policy_set"]["policy_set_version"] if prior else None,
                "to_version": version,
                "before_revision_id": before_summary["policy_revision_id"]
                if before_summary
                else None,
                "after_revision_id": summary["policy_revision_id"] if current else None,
                "detail_revision_id": summary["policy_revision_id"],
                "summary": {
                    "ADD": "策略正式生效",
                    "MODIFY": "策略正式版本更新",
                    "RETIRE": "策略退出生效集合",
                    "RESTORE": "策略再次生效",
                }[change_type],
                "changed_paths": paths,
            },
        )
        day = semantic_day(datetime.fromisoformat(at)).isoformat()
        for shell in set(summary["shell_ids"]) | set(previous["shell_ids"] if previous else []):
            records.append(
                {
                    "kind": "policy_change",
                    "ticker": ticker,
                    "id": identity + ":" + shell,
                    "parent": shell,
                    "source_id": summary["policy_id"],
                    "route": key,
                    "sort": at,
                    "day": day,
                    "data": change,
                }
            )
    return records, metrics


def consumption_projection(store, capture):
    """Refresh negative evidence only after the runtime receipt ledger catches up.

    Admission is immutable per activation revision. Restarting or restoring the
    same revision cannot manufacture a new, post-capture first activation.
    """
    tables = json.loads(capture.get("tables_json", "[]"))
    covered = capture.get("complete") is True and "runtime_v2_policy_activations" in tables
    start, end = capture.get("started_at"), capture.get("end_at")
    records = []
    with store.connect() as db:
        catalog = db.execute(
            "SELECT ticker,id,payload FROM objects WHERE kind='policy_catalog' AND valid_to IS NULL"
        ).fetchall()
    for ticker, key, payload in catalog:
        summary = json.loads(payload)
        before = dict(summary)
        consumed = store.get("policy_consumption", ticker, key)
        admission = store.get("policy_admission", ticker, key) or {}
        first = admission.get("first_activated_at")
        if consumed:
            summary.update(
                consumed=available(True),
                consumed_at=available(consumed["activated_at"]),
                effective=available(False),
            )
        elif (
            covered
            and start
            and end
            and first
            and datetime.fromisoformat(start)
            <= datetime.fromisoformat(first)
            <= datetime.fromisoformat(end)
        ):
            summary.update(
                consumed=available(False),
                consumed_at=missing(),
                effective=available(summary["lifecycle"] == "ACTIVE"),
            )
        else:
            summary.update(
                consumed=missing(),
                consumed_at=missing(),
                effective=available(False) if summary["lifecycle"] == "RETIRED" else missing(),
            )
        if summary != before:
            records.append(
                {
                    "kind": "policy_catalog",
                    "ticker": ticker,
                    "id": key,
                    "data": validate("PolicySummary", summary),
                }
            )
    return records
