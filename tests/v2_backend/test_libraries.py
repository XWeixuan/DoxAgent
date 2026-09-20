import json
import sqlite3

import pytest

from doxagent.event_library.repository import EventLibraryRepository
from doxagent.v2_read.libraries import LibraryIndexer
from tests.test_codex_event_library_incremental import _publish_v1


def _change_event_state(
    path,
    *,
    status: str,
    retire_memberships: bool,
    corrupt_title: bool,
    corrupt_fact: bool = False,
) -> None:
    with sqlite3.connect(path) as db:
        if status != "ACTIVE":
            db.execute(
                "UPDATE canonical_event_states SET status=? WHERE ticker='MU' AND event_no=1",
                (status,),
            )
        if retire_memberships:
            db.execute(
                "UPDATE event_fact_memberships SET valid_to_version=0 "
                "WHERE ticker='MU' AND event_no=1"
            )
        if corrupt_title:
            row = db.execute(
                "SELECT revision_no,payload_json FROM canonical_event_revisions "
                "WHERE ticker='MU' AND event_no=1 ORDER BY library_version DESC LIMIT 1"
            ).fetchone()
            payload = json.loads(row[1])
            payload["title"] = ""
            db.execute(
                "UPDATE canonical_event_revisions SET payload_json=? "
                "WHERE ticker='MU' AND event_no=1 AND revision_no=?",
                (json.dumps(payload), row[0]),
            )
        if corrupt_fact:
            row = db.execute(
                "SELECT revisions.fact_no,revisions.revision_no,revisions.payload_json "
                "FROM canonical_fact_revisions revisions "
                "JOIN event_fact_memberships memberships "
                "ON memberships.ticker=revisions.ticker "
                "AND memberships.fact_no=revisions.fact_no "
                "WHERE memberships.ticker='MU' AND memberships.event_no=1 "
                "ORDER BY revisions.library_version DESC LIMIT 1"
            ).fetchone()
            payload = json.loads(row[2])
            payload["proposition"] = ""
            db.execute(
                "UPDATE canonical_fact_revisions SET payload_json=? "
                "WHERE ticker='MU' AND fact_no=? AND revision_no=?",
                (json.dumps(payload), row[0], row[1]),
            )


def test_independent_branches_same_numeric_ids_have_different_birth_keys(tmp_path):
    snapshots = []
    for name in ("a", "b"):
        root = tmp_path / name
        path = root / "US" / "MU" / "event_library.sqlite3"
        repository = EventLibraryRepository(path)
        _publish_v1(repository, root)
        reference, records = LibraryIndexer(root).index("MU", 1)
        snapshots.append((reference, records))
    first = next(r["data"] for r in snapshots[0][1] if r["kind"] == "event")
    second = next(r["data"] for r in snapshots[1][1] if r["kind"] == "event")
    assert first["event_id"] == second["event_id"]
    assert first["event_key"] != second["event_key"]
    assert snapshots[0][0]["library_snapshot_id"] != snapshots[1][0]["library_snapshot_id"]
    assert not any(r["kind"] == "lineage_gap" for r in snapshots[0][1])
    copied = tmp_path / "copy" / "US" / "MU" / "event_library.sqlite3"
    copied.parent.mkdir(parents=True)
    with sqlite3.connect(tmp_path / "a" / "US" / "MU" / "event_library.sqlite3") as source:
        with sqlite3.connect(copied) as target:
            source.backup(target)
    reference, records = LibraryIndexer(tmp_path / "copy").index("MU", 1)
    assert reference == snapshots[0][0]
    assert (
        next(r["data"] for r in records if r["kind"] == "event")["event_key"] == first["event_key"]
    )


def test_invalid_empty_retired_event_is_isolated_from_library_activation(tmp_path):
    root = tmp_path / "retired-invalid"
    path = root / "US" / "MU" / "event_library.sqlite3"
    repository = EventLibraryRepository(path)
    _publish_v1(repository, root)
    _change_event_state(
        path,
        status="MERGED",
        retire_memberships=True,
        corrupt_title=False,
        corrupt_fact=True,
    )

    reference, records = LibraryIndexer(root).index("MU", 1)

    assert reference["library_version"] == 1
    assert not [
        record
        for record in records
        if record["kind"] == "event" and record["data"]["event_id"] == "E1"
    ]
    assert not [
        record
        for record in records
        if record["kind"] == "fact" and record.get("parent", "").endswith(":E1")
    ]
    assert any(
        record["kind"] == "event" and record["data"]["event_id"] == "E2" for record in records
    )
    assert [record["data"] for record in records if record["kind"] == "lineage_gap"] == [
        {
            "reason": "RETIRED_EVENT_INDEX_SKIPPED",
            "event_id": "E1",
            "status": "MERGED",
            "library_snapshot_id": reference["library_snapshot_id"],
            "error_type": "ValidationError",
        }
    ]


def test_invalid_active_event_still_blocks_library_activation(tmp_path):
    root = tmp_path / "active-invalid"
    path = root / "US" / "MU" / "event_library.sqlite3"
    repository = EventLibraryRepository(path)
    _publish_v1(repository, root)
    _change_event_state(path, status="ACTIVE", retire_memberships=False, corrupt_title=True)

    with pytest.raises(ValueError):
        LibraryIndexer(root).index("MU", 1)


def test_valid_empty_retired_event_remains_indexed(tmp_path):
    root = tmp_path / "retired-valid"
    path = root / "US" / "MU" / "event_library.sqlite3"
    repository = EventLibraryRepository(path)
    _publish_v1(repository, root)
    _change_event_state(path, status="SUPPRESSED", retire_memberships=True, corrupt_title=False)

    _, records = LibraryIndexer(root).index("MU", 1)

    event = next(
        record
        for record in records
        if record["kind"] == "event" and record["data"]["event_id"] == "E1"
    )
    assert event["data"]["status"] == "SUPPRESSED"
    assert event["data"]["active_fact_count"] == 0
    assert not [
        record
        for record in records
        if record["kind"] == "lineage_gap"
        and record["data"].get("reason") == "RETIRED_EVENT_INDEX_SKIPPED"
    ]
