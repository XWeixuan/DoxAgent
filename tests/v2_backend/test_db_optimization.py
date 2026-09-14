import asyncio
import json
import sqlite3
import time
from datetime import datetime, UTC, timedelta

import pytest

from doxagent.api_v2.auth import Principal
from doxagent.api_v2.query_runner import QueryRunner
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.cli import backup
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.repository import ReadStore


def test_noop_preserves_public_sequence_and_sort_only_change_is_visible(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    record = {"kind": "message", "ticker": "MU", "id": "m", "data": {"body": "same"}, "sort": "a"}
    seq = store.ingest("bus", "1", [record], position=1)
    assert store.ingest("bus", "2", [record], position=2) == seq
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM commits").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM objects").fetchone()[0] == 1
    assert store.position("bus") == 2
    changed = store.ingest("bus", "3", [{**record, "sort": "b"}], position=3)
    assert changed > seq
    assert store.page("message", "MU", seq)[0]["sort"] == "a"
    assert store.page("message", "MU", changed)[0]["sort"] == "b"


def test_receipt_archive_keeps_head_identity_and_can_replay(tmp_path):
    path = tmp_path / "runtime.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE runtime_v2_cases(case_id TEXT PRIMARY KEY,ticker TEXT,payload_json TEXT)")
    source = SourceOutbox(path, "runtime")
    source.migrate()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO runtime_v2_cases VALUES('c','MU','{}')")
        db.execute("UPDATE runtime_v2_cases SET payload_json=payload_json")
        db.execute("UPDATE v2_source_outbox SET recorded_at=?", ((datetime.now(UTC)-timedelta(days=9)).isoformat(),))
    assert source.head() == 1
    event = source.event(1)
    source.acknowledge("read", 1)
    checkpoint = tmp_path / "checkpoint.db"
    backup(path, checkpoint)
    source.acknowledge("read", 1, repair_pin=1)
    before = (datetime.now(UTC)-timedelta(days=8)).isoformat()
    assert source.archive(checkpoint, before=before) == 0
    source.acknowledge("read", 1)
    assert source.archive(checkpoint, before=before) == 1
    assert source.head() == 1
    assert source.event(1) == event
    assert source.read(0) == [event]


@pytest.mark.asyncio
async def test_spawned_query_worker_uses_verified_principal_and_closes(tmp_path, monkeypatch):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    monkeypatch.setenv("DOXAGENT_V2_READ_SQLITE_PATH", str(store.path))
    monkeypatch.setenv("DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH", str(tmp_path / "runtime.db"))
    runner = QueryRunner(workers=1)
    try:
        await runner.start()
        result = await runner.run({"kind": "http", "url": "/api/doxagent/v2/auth/me", "headers": {},
                                   "principal": Principal("alice", "DEVELOPER", time.time()+60)})
        assert result[0] == 200
        assert b"alice" in result[2]
        assert runner.available.qsize() == 1
    finally:
        await runner.close()
    assert not any(slot.process.is_alive() for slot in runner.slots)


def test_native_large_input_roundtrip_capture_and_backup(tmp_path):
    path = tmp_path / "runtime.db"
    journal = RuntimeJournal(path)
    source = SourceOutbox(path,"runtime")
    source.migrate()
    inputs = {"source": {"body": "long business evidence " * 3000}, "stream_offset": 17, "sweep_id": "s1"}
    journal.put_task("case","MU","CASE",inputs)
    assert journal.put_task("case","MU","CASE",inputs)["inputs"] == inputs
    assert journal.task_highwater("MU") == 17
    with sqlite3.connect(path) as db:
        assert len(db.execute("SELECT inputs FROM runtime_tasks").fetchone()[0]) < 1000
    event = source.read(0)[0]
    assert json.loads(event["row"]["inputs"]) == inputs
    target = tmp_path / "backup" / "runtime.db"
    backup(path,target)
    assert RuntimeJournal(target,initialize=False).get_task("case")["inputs"] == inputs


def test_bus_aggregate_corrections_and_batch_metrics(tmp_path):
    from doxagent.api_v2.bus_metrics import counts
    from doxagent.v2_read.metrics import Metrics
    from decimal import Decimal
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    message = {"kind":"message","ticker":"MU","id":"m","day":"2026-09-12","source_id":"s","route":"NORMAL","data":{"source":{"kind":"NEWS"}}}
    seq = store.ingest("bus","1",[message,
        {"kind":"message_raw_link","ticker":"MU","id":"raw","parent":"m","data":{}},
        {"kind":"body_attempt","ticker":"MU","id":"a","parent":"raw","day":"2026-09-13","data":{"succeeded":True}}],
        contributions=[{"metric":"messages","ticker":"MU","entity":"m","day":"2026-09-12","dimensions":{},"value":1}])
    assert counts(store,"MU",seq,{},["2026-09-12"]) == (Decimal(1),None)
    assert counts(store,"MU",seq,{},["2026-09-13"]) == (Decimal(0),Decimal(1))
    later = store.ingest("bus","2",[{**message,"route":"BACKFILL","day":"2026-09-14"}])
    assert counts(store,"MU",later,{"route":"NORMAL"},None) == (Decimal(0),None)
    assert counts(store,"MU",later,{"route":"BACKFILL"},None) == (Decimal(1),Decimal(1))
    assert counts(store,"MU",seq,{},["2026-09-12"]) == (Decimal(1),None)
    service = Metrics(store)
    expected = service.metric("messages",["MU"],seq,days=["2026-09-12"],previous_days=["2026-09-11"])
    service.prime(["messages"],["MU"],seq,[["2026-09-12"],["2026-09-11"]])
    assert service.metric("messages",["MU"],seq,days=["2026-09-12"],previous_days=["2026-09-11"]) == expected
