import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import RoundStatus
from doxagent.semantic_clock import semantic_day
from doxagent.ticker_initialization import InitializationRepository, NodeSpec
from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


def test_progress_reads_used_fields_without_hydrating_worker_receipts(tmp_path, monkeypatch):
    from doxagent.v2_read.content_files import ContentFiles, ContentUnavailable
    from doxagent.v2_read.initialization import progress

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    run = dict(ticker="MU", initialization_id="init-mu", status="SUCCEEDED", state_seq=5,
               manual_resume_required=False, error=None)
    node = dict(key="d3", block="D3", status="SUCCEEDED",
                receipt={"worker_output": "r" * 20000},
                result={"artifacts": {"large": "a" * 20000},
                        "quality_annotations": ["PARTIAL"]})
    records = [dict(kind="native:initialization_nodes", ticker="MU", id="d3",
                    parent="init-mu", data=node)]
    for index, (kind, at) in enumerate([
        ("node.started", "2026-10-18T00:00:00Z"),
        ("node.settled", "2026-10-18T00:01:00Z"),
    ]):
        records.append(dict(kind="native:initialization_events", ticker="MU", id=str(index),
                            parent="init-mu", data=dict(kind=kind, created_at=at,
                            payload={"node": "d3", "unused": "e" * 20000})))
    store.ingest("test", "seed", records)
    with sqlite3.connect(store.path) as db:
        raw = json.loads(db.execute(
            "SELECT payload FROM objects WHERE kind='native:initialization_nodes'"
        ).fetchone()[0])
    # V4 serves stage metadata only; source receipts/artifacts stay with their
    # owning business repository. Also exercise a retained V3 externalized row.
    assert "receipt" not in raw
    assert raw["result"] == {"quality_annotations": ["PARTIAL"]}
    raw = json.loads(store.content_codec.encode(node))
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE objects SET payload=? WHERE kind='native:initialization_nodes'",
                   (json.dumps(raw),))
    receipt = raw["receipt"]["$doxagent_content_v1"]
    result = raw["result"]["$doxagent_content_v1"]
    original = ContentFiles.read
    blocked = {receipt}

    def checked(self, identity, *args, **kwargs):
        if identity in blocked:
            raise ContentUnavailable("unavailable test content")
        return original(self, identity, *args, **kwargs)

    monkeypatch.setattr(ContentFiles, "read", checked)
    value = progress(store, run, dict(kind="native:initialization_runs"))["data"]
    stage = next(s for s in value["steps"] if s["step_key"] == "POLICIES")
    assert stage["status"] == "SUCCEEDED"
    assert stage["quality_annotations"] == ["PARTIAL"]
    assert stage["duration_seconds"]["value"] == 60
    assert value["failed_node_keys"] == []
    # Results used by progress retain normal native-content verification.
    blocked.add(result)
    with pytest.raises(ContentUnavailable):
        progress(store, run, dict(kind="native:initialization_runs"))


def test_progress_retains_all_failed_nodes_and_recorded_stage_times(tmp_path):
    repository = InitializationRepository(tmp_path / "initialization.db")
    source = SourceOutbox(repository.path, "initialization")
    source.migrate()
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    run = repository.submit(
        "MU",
        datetime(2026, 9, 8, tzinfo=UTC),
        [NodeSpec(key=f"d1-{n}", block="D1") for n in range(12)],
    )
    lease = repository.claim("fixture")
    for n in range(12):
        repository.begin(lease, f"d1-{n}", {"adapter": "fixture"})
        repository.fail(lease, f"d1-{n}", "fixture failure")
    repository.finish(lease, error="fixture failed")
    worker = ProjectionWorker(store, [source], DomainProjectors(store))
    for _ in range(10):
        worker.tick(limit=500)
        with store.connect(write=True) as db:
            db.execute("UPDATE gaps SET next_attempt_at=''")
    progress = store.get("initialization", "MU", run.initialization_id)
    assert progress["status"] == "FAILED"
    assert len(progress["failed_node_keys"]) == 12
    assert len(progress["steps"]) == 6
    assert progress["steps"][0]["duration_seconds"]["state"] == "AVAILABLE"
    assert progress["manual_resume_allowed"] is True


def _failed_initialization(repository: InitializationRepository, ticker: str = "MU"):
    repository.submit(
        ticker,
        datetime(2026, 9, 21, 12, tzinfo=UTC),
        [NodeSpec(key="cdecr", block="CDECR")],
    )
    lease = repository.claim("fixture")
    assert lease is not None
    repository.begin(lease, "cdecr", {})
    repository.fail(lease, "cdecr", "broken")
    return repository.finish(lease, error="broken")


def _drain(worker: ProjectionWorker, store: ReadStore) -> None:
    for _ in range(10):
        worker.tick(limit=500)
        with store.connect(write=True) as db:
            db.execute("UPDATE gaps SET next_attempt_at='' ")


def _highwater(store: ReadStore) -> int:
    with store.connect() as db:
        return store.highwater(db)


def test_repair_rounds_project_once_across_status_updates_and_new_rounds(tmp_path):
    repository = InitializationRepository(tmp_path / "initialization.db")
    source = SourceOutbox(repository.path, "initialization")
    source.migrate()
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    failed = _failed_initialization(repository)
    repairs = RepairRepository(repository)
    incident = repairs.open_incident(
        failed.initialization_id,
        expected_state_seq=failed.state_seq,
    )
    first = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    worker = ProjectionWorker(store, [source], DomainProjectors(store))
    _drain(worker, store)

    day = semantic_day(first.created_at).isoformat()
    seq = _highwater(store)
    assert Metrics(store).value("nonroutine_repairs", ["MU"], seq, days=[day]) == 1

    repairs.update_round(first.round_id, status=RoundStatus.AGENT_RUNNING)
    repairs.update_round(first.round_id, status=RoundStatus.VERIFIED)
    _drain(worker, store)
    seq = _highwater(store)
    assert Metrics(store).value("nonroutine_repairs", ["MU"], seq, days=[day]) == 1

    repairs.finish_round(first.round_id, result="still broken", exit_code=1)
    second = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    assert second.round_id != first.round_id
    _drain(worker, store)
    seq = _highwater(store)
    assert Metrics(store).value("nonroutine_repairs", ["MU"], seq, days=[day]) == 2

    repairs.finish_round(second.round_id, result="still broken", exit_code=1)
    third = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    next_created_at = third.created_at + timedelta(days=1)
    repairs.update_round(third.round_id, created_at=next_created_at)
    _drain(worker, store)
    seq = _highwater(store)
    next_day = semantic_day(next_created_at).isoformat()
    assert Metrics(store).value("nonroutine_repairs", ["MU"], seq, days=[day]) == 2
    assert Metrics(store).value("nonroutine_repairs", ["MU"], seq, days=[next_day]) == 1


def test_existing_repair_rounds_backfill_incident_before_round(tmp_path):
    repository = InitializationRepository(tmp_path / "initialization.db")
    failed = _failed_initialization(repository)
    repairs = RepairRepository(repository)
    incident = repairs.open_incident(
        failed.initialization_id,
        expected_state_seq=failed.state_seq,
    )
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    source = SourceOutbox(repository.path, "initialization")
    source.migrate()
    assert source.backfill("initialization_repair_incidents", limit=500) == 1
    assert source.backfill("initialization_repair_rounds", limit=500) == 1
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    worker = ProjectionWorker(store, [source], DomainProjectors(store))
    _drain(worker, store)

    assert store.get(
        "native:initialization_repair_incidents", "MU", incident.incident_id
    ) is not None
    day = semantic_day(repair_round.created_at).isoformat()
    assert Metrics(store).value(
        "nonroutine_repairs", ["MU"], _highwater(store), days=[day]
    ) == 1
