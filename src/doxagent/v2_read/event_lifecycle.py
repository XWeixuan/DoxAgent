"""Formal Event/Fact transitions keep birth identity through branch copies and revisions."""

from datetime import datetime
from hashlib import sha256

from doxagent.semantic_clock import semantic_day

from .lifecycle import rows
from .repository import encode


def activate(store, ticker, activation, at):
    snapshot = activation["event_library"]["library_snapshot_id"]
    previous = store.get("event_effective_snapshot", ticker, "active")
    if previous and previous["snapshot"] == snapshot:
        return [], []
    events = rows(store, "event", ticker, snapshot)
    incoming = {event["event_key"]: event for event in events.values()}
    prior = rows(store, "event_catalog", ticker)
    fact_incoming = {}
    for event in incoming.values():
        for fact in rows(store, "fact", ticker, snapshot + ":" + event["event_id"]).values():
            key = fact["fact_key"]
            active = (
                event["status"] == "ACTIVE"
                and fact["member_of_event"]
                and fact["lifecycle"] == "ACTIVE"
            )
            if key not in fact_incoming or active:
                fact_incoming[key] = {**fact, "active": active}
    records, contributions = [], []
    for label, values, old in (
        ("event", incoming, prior),
        ("fact", fact_incoming, rows(store, "fact_catalog", ticker)),
    ):
        for key in sorted(set(values) | set(old)):
            before, after = old.get(key), values.get(key)
            was_active = bool(
                before
                and (
                    "ACTIVE" in before["matched_filters"] if label == "event" else before["active"]
                )
            )
            is_active = bool(
                after and (after["status"] == "ACTIVE" if label == "event" else after["active"])
            )
            if after is None:
                after = dict(before)
                if label == "fact":
                    after["active"] = False
                else:
                    # An absent branch identity retains its last exact detail.
                    after["matched_filters"] = []
            if label == "event":
                after["matched_filters"] = ["ACTIVE"] if is_active else []
            records.append(
                {
                    "kind": label + "_catalog",
                    "ticker": ticker,
                    "id": key,
                    "data": after,
                    "route": "ACTIVE" if is_active else "RETIRED",
                }
            )
            revision_key = label + "_revision_id"
            change = (
                "ADDED"
                if is_active and not before
                else "RETIRED"
                if was_active and not is_active
                else (
                    "MODIFIED"
                    if is_active
                    and before
                    and (not was_active or before[revision_key] != after[revision_key])
                    else None
                )
            )
            if not at or not change:
                continue
            identity = sha256(encode([snapshot, label, key, change]).encode()).hexdigest()
            day = semantic_day(datetime.fromisoformat(at)).isoformat()
            records.append(
                {
                    "kind": label + "_change",
                    "ticker": ticker,
                    "id": identity,
                    "parent": key,
                    "route": change,
                    "sort": at,
                    "day": day,
                    "data": {"change_id": identity, "type": change, "occurred_at": at},
                }
            )
            contributions.append(
                {
                    "metric": change.lower() + "_" + label + "s",
                    "ticker": ticker,
                    "entity": identity,
                    "day": day,
                    "value": "1",
                    "dimensions": {"business_key": key},
                }
            )
    records.append(
        {
            "kind": "event_effective_snapshot",
            "ticker": ticker,
            "id": "active",
            "data": {"snapshot": snapshot},
        }
    )
    return records, contributions
