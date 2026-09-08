from datetime import UTC, datetime

from doxagent.ticker_initialization import InitializationRepository, NodeSpec
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
