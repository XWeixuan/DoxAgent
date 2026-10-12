"""Durable evidence for the exact Reference input handed to the O3 maintenance call."""

import hashlib
import json


def snapshot(compiler, ticker, version):
    events = compiler.reference_events(ticker, version) if version else []
    return {
        "version": version,
        "events": [event.model_dump(mode="json") for event in events],
        "rendered": {
            event.event_id: compiler._render_reference_events(ticker, [event]) for event in events
        },
    }


def freeze_before(journal, task, compiler, version):
    if journal.get("reference_baselines", task["id"]) is None:
        journal.set("reference_baselines", task["id"], snapshot(compiler, task["ticker"], version))


def actual_delta(journal, task, compiler, before_version, after_version):
    from doxagent.event_library.contracts import CanonicalEvent, ReferenceViewDeltaSnapshot

    before = journal.get("reference_baselines", task["id"])
    if before is None:
        before = snapshot(compiler, task["ticker"], before_version)
    after = snapshot(compiler, task["ticker"], after_version)
    changed = [
        CanonicalEvent.model_validate(event)
        for event in after["events"]
        if before["rendered"].get(event["event_id"]) != after["rendered"][event["event_id"]]
    ]

    def identity(value):
        return hashlib.sha256(
            json.dumps(
                [task["ticker"], value], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    return ReferenceViewDeltaSnapshot(
        ticker=task["ticker"],
        from_library_version=before_version,
        to_library_version=after_version,
        removed_event_ids=sorted(set(before["rendered"]) - set(after["rendered"])),
        reference_view_delta=compiler._render_reference_events(task["ticker"], changed)
        if changed
        else "",
        before_reference_snapshot_id=identity(before),
        after_reference_snapshot_id=identity(after),
    )


def prepare(journal, task, compiler, root, day, delta):
    prior = journal.get("reference_deliveries", task["id"])
    if prior:
        return prior
    snapshots = {}
    for side, version in (
        ("before", delta.from_library_version),
        ("after", delta.to_library_version),
    ):
        snapshots[side] = snapshot(compiler, task["ticker"], version)
        if side == "before":
            snapshots[side] = journal.get("reference_baselines", task["id"]) or snapshots[side]
    value = {
        "delta_id": task["id"],
        "ticker": task["ticker"],
        "semantic_day": str(day),
        "root": str(root),
        "status": "PREPARED",
        "control_epoch": task.get("inputs", {}).get("control_epoch"),
        "prepared_at": journal.clock().isoformat(),
        "submitted_feed": delta.model_dump(mode="json"),
        **snapshots,
    }
    journal.set("reference_deliveries", task["id"], value)
    return value


def settle(journal, identity, status):
    value = journal.get("reference_deliveries", identity)
    if value and value["status"] != "SUCCEEDED":
        journal.set(
            "reference_deliveries",
            identity,
            {**value, "status": status, "settled_at": journal.clock().isoformat()},
        )
