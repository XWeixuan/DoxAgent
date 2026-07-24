from __future__ import annotations

from cdecr.wire import compact_json, intern_repeated_ids, wire_ref_metadata


def test_repeated_long_ids_are_interned_only_when_payload_shrinks() -> None:
    canonical_id = "canonical-field:company:micron-technology:000001"
    payload = {
        "tasks": [
            {"issuer_id": canonical_id, "company_id": canonical_id},
            {"issuer_id": canonical_id, "candidate_event_id": "a1"},
        ]
    }

    compact = intern_repeated_ids(payload)

    assert compact["refs"] == {"r1": canonical_id}
    assert compact["tasks"][0]["issuer_id"] == "r1"  # type: ignore[index]
    assert compact["tasks"][0]["company_id"] == "r1"  # type: ignore[index]
    assert compact["tasks"][1]["candidate_event_id"] == "a1"  # type: ignore[index]
    assert len(compact_json(compact)) < len(compact_json(payload))
    assert wire_ref_metadata(compact)["wire_ref_count"] == 1


def test_single_or_short_ids_are_not_interned() -> None:
    payload = {
        "event_id": "a1",
        "issuer_id": "canonical-field:company:micron-technology:000001",
    }

    assert intern_repeated_ids(payload) == payload
    assert wire_ref_metadata(payload) == {"wire_ref_count": 0}
