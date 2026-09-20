from datetime import UTC, datetime, timedelta

from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import RoundStatus
from doxagent.semantic_clock import semantic_day
from doxagent.ticker_initialization import InitializationRepository, NodeSpec
from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


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
