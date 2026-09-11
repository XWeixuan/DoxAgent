import sqlite3

from doxagent.event_library.repository import EventLibraryRepository
from doxagent.v2_read.libraries import LibraryIndexer
from tests.test_codex_event_library_incremental import _publish_v1


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
