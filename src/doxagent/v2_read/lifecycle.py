"""Lifecycle transitions only when a formal activation becomes effective."""

import json
from datetime import datetime
from hashlib import sha256

from doxagent.api_v2.dto import available, missing, validate
from doxagent.persistent_runtime_v2 import policy_consumption
from doxagent.semantic_clock import semantic_day

from .repository import encode


def rows(store, kind, ticker, parent=None):
    # Worker-side enumeration of one immutable version, never an HTTP history scan.
    with store.connect() as db:
        sql = "SELECT id,payload FROM object_current WHERE kind=? AND ticker=? AND valid_to IS NULL"
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
        d["summary"]["policy_id"]
        + ":"
        + d["summary"].get("policy_activation_revision", d["summary"]["policy_revision_id"]): d
        for d in details.values()
    }
    catalog = rows(store, "policy_catalog", ticker)
    evidence = direction_evidence(store, ticker) if any(
        "directions" in p for p in [*catalog.values(), *(d["summary"] for d in incoming.values())]
    ) else None
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
        if "directions" in summary:
            summary = direction_summary(store, ticker, summary, evidence=evidence)
        consumed = None if "directions" in summary else store.get("policy_consumption", ticker, key)
        if consumed:
            summary.update(
                consumed=available(True),
                consumed_at=available(consumed["activated_at"]),
                effective=available(False)
                if policy_consumption.POLICY_AUTO_FREEZE_ENABLED
                else available(summary["lifecycle"] == "ACTIVE"),
            )
        if not policy_consumption.POLICY_AUTO_FREEZE_ENABLED:
            summary["effective"] = available(summary["lifecycle"] == "ACTIVE")
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
            change_type = "MODIFY" if paths else None
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
        for shell in (
            set(summary["shell_ids"]) | set(previous["shell_ids"] if previous else [])
        ) or {"ALL"}:
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
        # Only admissions crossing a proof boundary or invalidated negative evidence need work.
        if covered and start and end:
            catalog = db.execute(
                "SELECT p.ticker,p.id,p.payload FROM object_current p JOIN object_current a "
                "ON a.kind='policy_admission' AND a.ticker=p.ticker AND a.id=p.id "
                "WHERE p.kind='policy_catalog' AND json_type(p.payload,'$.directions') IS NULL "
                "AND json_extract(p.payload,'$.consumed.value') IS NULL "
                "AND julianday(json_extract(a.payload,'$.first_activated_at')) BETWEEN julianday(?) AND julianday(?)",
                (start, end),
            ).fetchall()
        else:
            catalog = db.execute(
                "SELECT ticker,id,payload FROM object_current WHERE kind='policy_catalog' "
                "AND json_type(payload,'$.directions') IS NULL "
                "AND json_extract(payload,'$.consumed.value')=0"
            ).fetchall()
    for ticker, key, payload in catalog:
        summary = json.loads(payload)
        before = dict(summary)
        if "directions" in summary:
            continue
        consumed = store.get("policy_consumption", ticker, key)
        admission = store.get("policy_admission", ticker, key) or {}
        first = admission.get("first_activated_at")
        if consumed:
            summary.update(
                consumed=available(True),
                consumed_at=available(consumed["activated_at"]),
                effective=available(False)
                if policy_consumption.POLICY_AUTO_FREEZE_ENABLED
                else available(summary["lifecycle"] == "ACTIVE"),
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
        if not policy_consumption.POLICY_AUTO_FREEZE_ENABLED:
            summary["effective"] = available(summary["lifecycle"] == "ACTIVE")
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


def direction_evidence(store, ticker):
    """One event-local snapshot shared by all Policy summaries of a ticker."""
    with store.connect() as db:
        states = {
            r[0]: json.loads(r[1]) for r in db.execute(
                "SELECT id,payload FROM object_current WHERE kind='policy_direction_usage' AND ticker=?",
                (ticker,),
            )
        }
        head = db.execute(
            "SELECT 1 FROM object_current WHERE kind='native:runtime_values' AND ticker=? AND id=?",
            (ticker, "policy_authorization_head:" + ticker),
        ).fetchone() is not None
        unresolved = {
            r[0] for r in db.execute(
                "SELECT json_extract(payload,'$.policy_id') FROM object_current "
                "WHERE kind='native:runtime_values' AND ticker=? "
                "AND id LIKE 'policy_consumption_migration:%' "
                "AND json_extract(payload,'$.status')='UNRESOLVED'", (ticker,),
            )
        }
    return states, head, unresolved


def direction_summary(store, ticker, summary, *, pending_state=None, admitted=False, evidence=None):
    summary = dict(summary)
    usage, head, migrations = evidence if evidence is not None else direction_evidence(store, ticker)
    states = [
        usage.get(encode([ticker, summary["policy_id"], direction]))
        for direction in summary["directions"]
    ]
    if pending_state:
        states = [
            pending_state if direction == pending_state["direction"] else state
            for direction, state in zip(summary["directions"], states, strict=True)
        ]
    known = admitted or head or all(state is not None for state in states)
    consumed = [
        direction
        for direction, state in zip(summary["directions"], states, strict=True)
        if state and state["consumed"]
    ]
    unresolved = summary["policy_id"] in migrations
    executable = (
        []
        if unresolved
        else [
            direction
            for direction, state in zip(summary["directions"], states, strict=True)
            if direction not in consumed and (known or state is not None)
        ]
    )
    summary.update(
        consumed_directions=consumed,
        executable_directions=executable,
        consumed=available(len(consumed) == len(states)) if known else missing(),
        effective=available(False)
        if summary["lifecycle"] != "ACTIVE" or unresolved
        else available(bool(executable))
        if known or executable
        else missing(),
    )
    return summary
