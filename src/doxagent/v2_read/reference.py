"""Index captured O3 input snapshots; never diff an old version against today's head."""

from hashlib import sha256

from doxagent.api_v2.dto import validate

from .libraries import LibraryIndexer
from .lifecycle import changed_paths
from .repository import encode


def project(store, value):
    ticker, identity, day = value["ticker"], value["delta_id"], value["semantic_day"]
    evidence = {"kind": "reference_input", "ticker": ticker, "id": identity, "data": value}
    prior = store.get("reference_day_chain", ticker, day)
    if prior and prior["prepared_at"] > value["prepared_at"]:
        return [evidence]
    if prior:
        value = {
            **value,
            "before": prior["before"],
            "before_root": prior.get("before_root", prior["root"]),
        }
    records, refs, snapshot_ids, events = [evidence], {}, {}, {}
    records.append({"kind": "reference_day_chain", "ticker": ticker, "id": day, "data": value})
    for side in ("before", "after"):
        captured = value[side]
        snapshot_ids[side] = sha256(encode([ticker, captured]).encode()).hexdigest()
        ref, indexed = (
            LibraryIndexer(value.get(side + "_root", value["root"])).index(
                ticker, captured["version"]
            )
            if captured["version"]
            else (None, [])
        )
        refs[side] = ref
        records.extend(indexed)
        by_id = {r["data"]["event_id"]: r["data"] for r in indexed if r["kind"] == "event"}
        facts = {
            r["data"]["fact"]["fact_id"]: r["data"]["fact_key"]
            for r in indexed
            if r["kind"] == "fact"
        }
        events[side] = {}
        for event in captured["events"]:
            key = by_id[event["event_id"]]["event_key"]
            revision = sha256(encode([key, event]).encode()).hexdigest()
            events[side][key] = {"event": event, "revision": revision}
            parent = snapshot_ids[side] + ":" + event["event_id"]
            detail = {
                "event_key": key,
                "library": ref,
                "reference_snapshot_id": snapshot_ids[side],
                "event_revision_id": revision,
                "event": {k: v for k, v in event.items() if k != "facts"},
            }
            records.append(
                {"kind": "reference_event", "ticker": ticker, "id": parent, "data": detail}
            )
            for ordinal, fact in enumerate(event["facts"]):
                fact_key = facts[fact["fact_id"]]
                records.append(
                    {
                        "kind": "fact",
                        "ticker": ticker,
                        "id": parent + ":" + fact_key,
                        "parent": parent,
                        "sort": f"{999999999 - ordinal:09d}",
                        "data": validate(
                            "FactRow",
                            {
                                "fact_key": fact_key,
                                "fact": fact,
                                "ordinal": ordinal,
                                "fact_revision_id": sha256(
                                    encode([fact_key, fact]).encode()
                                ).hexdigest(),
                                "lifecycle": "ACTIVE",
                                "member_of_event": True,
                            },
                        ),
                    }
                )
    changes = []
    for key in sorted(set(events["before"]) | set(events["after"])):
        before, after = events["before"].get(key), events["after"].get(key)
        event_id = (after or before)["event"]["event_id"]
        if (
            before
            and after
            and value["before"]["rendered"][event_id] == value["after"]["rendered"][event_id]
        ):
            continue
        selected = "after" if after else "before"
        chosen = after or before
        change_id = sha256(encode([identity, key]).encode()).hexdigest()
        change = validate(
            "ReferenceDeltaItem",
            {
                "change_id": change_id,
                "type": "add" if not before else "remove" if not after else "modify",
                "event_key": key,
                "event_id": event_id,
                "title": chosen["event"]["title"],
                "before_revision_id": before["revision"] if before else None,
                "after_revision_id": after["revision"] if after else None,
                "detail_revision_id": chosen["revision"],
                "detail_library_snapshot_id": refs[selected]["library_snapshot_id"],
                "detail_library_version": refs[selected]["library_version"],
                "changed_paths": changed_paths(
                    before["event"] if before else None, after["event"] if after else None
                ),
                "summary": "O3 实际 Reference 输入变化",
            },
        )
        changes.append(change)
        records.append(
            {
                "kind": "reference_change",
                "ticker": ticker,
                "id": identity + ":" + change_id,
                "parent": identity,
                "data": change,
            }
        )
        records.append(
            {
                "kind": "reference_change_sides",
                "ticker": ticker,
                "id": identity + ":" + change_id,
                "data": {
                    side: snapshot_ids[side] + ":" + event_id if events[side].get(key) else None
                    for side in ("before", "after")
                },
            }
        )
    data = {
        "delta_id": identity,
        "semantic_day": day,
        "from_library_version": value["before"]["version"],
        "to_library_version": value["after"]["version"],
        "from_library_snapshot_id": refs["before"]["library_snapshot_id"]
        if refs["before"]
        else None,
        "to_library_snapshot_id": refs["after"]["library_snapshot_id"],
        "before_reference_snapshot_id": snapshot_ids["before"],
        "after_reference_snapshot_id": snapshot_ids["after"],
    }
    records.append({"kind": "reference_delta", "ticker": ticker, "id": identity, "data": data})
    state = (
        "PENDING"
        if value["status"] == "PREPARED"
        else "FAILED"
        if value["status"] == "FAILED"
        else ("AVAILABLE" if changes else "NO_CHANGE")
    )
    day_data = {
        k: data[k]
        for k in (
            "delta_id",
            "semantic_day",
            "from_library_version",
            "to_library_version",
            "from_library_snapshot_id",
            "to_library_snapshot_id",
        )
    }
    day_data.update(state=state, reason="SOURCE_GAP" if state == "FAILED" else None)
    records.append(
        {
            "kind": "delta_day",
            "ticker": ticker,
            "id": day,
            "sort": day,
            "day": day,
            "data": validate("DeltaDay", day_data),
        }
    )
    return records
