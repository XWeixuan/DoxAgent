import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.repository import ReadStore


def test_source_capture_commits_with_fact_and_rolls_back_with_failure(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE runtime_v2_cases(case_id TEXT PRIMARY KEY,ticker TEXT,payload_json TEXT)"
        )
    outbox = SourceOutbox(path, "runtime")
    assert outbox.migrate(dry_run=True) == ["runtime_v2_cases"]
    outbox.migrate()
    with pytest.raises(RuntimeError), sqlite3.connect(path) as db:
        db.execute("INSERT INTO runtime_v2_cases VALUES('case-1','MU','{}')")
        raise RuntimeError("crash before commit")
    assert outbox.read(0) == []
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO runtime_v2_cases VALUES('case-1','MU','{}')")
    rows = outbox.read(0)
    assert len(rows) == 1 and rows[0]["row"]["case_id"] == "case-1"
    assert outbox.backfill("runtime_v2_cases", limit=1) == 1
    assert outbox.backfill("runtime_v2_cases", limit=1) == 0


def test_snapshot_does_not_drift_and_out_of_order_entity_does_not_regress(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    one = {"kind": "case", "ticker": "MU", "id": "one", "data": {"status": "RUNNING"}, "sort": "a"}
    before = store.ingest("runtime", "event-1", [one], position=1)
    after = store.ingest(
        "runtime", "event-3", [{**one, "data": {"status": "COMPLETED"}}], position=3
    )
    store.ingest("runtime", "event-2", [one], position=2)
    assert store.get("case", "MU", "one", before)["status"] == "RUNNING"
    assert store.get("case", "MU", "one")["status"] == "COMPLETED"
    assert store.ingest("runtime", "event-3", [], position=3) == after
    assert store.position("runtime") == 3
    with store.connect(write=True) as db:
        db.execute("CREATE TABLE marker(value INTEGER)")
    # Snapshot rows remain readable after ordinary connections have closed.
    assert store.page("case", "MU", before)[0]["data"]["status"] == "RUNNING"


def test_content_chunk_is_unicode_safe_and_tokens_are_owned_and_expire(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    reference = store.put_content("MU", "中文" * 100, "text/plain")
    _, body, offset = store.content("MU", reference["content_id"], size=7)
    assert body == "中文" and offset == 6
    with pytest.raises(KeyError):
        store.content("NVDA", reference["content_id"])
    now = datetime(2026, 9, 7, tzinfo=UTC)
    cursor = store.save_token("alice", "MU:messages", {"seq": 1}, now=now)
    with pytest.raises(ValueError):
        store.token(cursor, "bob", now=now)
    with pytest.raises(ValueError):
        store.token(cursor, "alice", scope="NVDA:messages", now=now)
    with pytest.raises(TimeoutError):
        store.token(cursor, "alice", now=now + timedelta(days=1))


def test_filtered_change_log_advances_past_unrelated_commits(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    for i in range(105):
        store.ingest(
            "bus", str(i), [{"kind": "unrelated", "ticker": "MU", "id": str(i), "data": {}}]
        )
    seq = store.ingest(
        "runtime", "last", [{"kind": "case", "ticker": "MU", "id": "last", "data": {}}]
    )
    assert store.changes("MU", ["case"], 0)[0]["seq"] == seq
