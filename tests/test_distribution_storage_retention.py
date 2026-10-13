from datetime import timedelta

from doxagent.message_bus_v2.admission import AdmissionContext
from doxagent.message_bus_v2.distribution_repository import DistributionRepository
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import RawMessageInput, UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service


def test_repeated_expired_feed_keeps_one_delivery_per_semantic_identity(tmp_path):
    bus = MessageBusV2Repository(tmp_path / "bus.db")
    service = MessageBusV2Service(bus, enrichment_queue_enabled=True)
    service.bootstrap()
    service.start_ticker("MU")
    source = bus.get_source("ctee_semiconductor")
    binding = service.configure_binding(ticker="MU", source_id=source.source_id,
                                        actor=UpdateActor.SYSTEM)
    repo = DistributionRepository(bus)
    context = AdmissionContext().model_dump(mode="json")
    message = RawMessageInput(external_id="old", title="Micron memory", source="CTEE",
                              url="https://example.com/old", raw_payload={},
                              published_at=utc_now() - timedelta(days=10))

    def poll(index, revision=1, item=message, admission=context, mode="REALTIME"):
        run = repo.get_or_create_run(work_key=f"poll-{index}", source=source, mode=mode,
                                    window_start=None, cutoff=None,
                                    roster=[(binding, revision, admission)])
        token = repo.claim_run(run["run_id"])
        repo.ingest(run["run_id"], token, source, [item], checkpoint={},
                    coverage="COMPLETE", done=True, deadline_seconds=180,
                    pipeline_version="test")

    for index in range(20):
        poll(index)
    with bus._connect() as db:
        assert db.execute("SELECT count(*) FROM distribution_deliveries").fetchone()[0] == 1
        assert db.execute("SELECT seen_count FROM distribution_delivery_keys").fetchone()[0] == 20
        assert db.execute("SELECT count(*) FROM distribution_snapshots").fetchone()[0] == 5
        assert db.execute("SELECT count(*) FROM distribution_observations").fetchone()[0] == 1
    assert bus.claim_enrichment_jobs(limit=5) == []
    poll(21, revision=2)
    poll(22, item=message.model_copy(update={"body": "A real revision"}))
    # Corrected dates must get a new article identity and become eligible.
    poll(23, item=message.model_copy(update={"published_at": utc_now()}))
    rows = repo.claim_deliveries()
    assert rows == []  # Eligible content still awaits its body job.
    with bus._connect() as db:
        assert db.execute("SELECT count(*) FROM distribution_deliveries").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM distribution_deliveries WHERE state='WAIT_BODY'").fetchone()[0] == 1
    sweep = AdmissionContext(mode="CLOSED_SWEEP", sweep_id="s", source_task_id="task",
                             window_start=message.published_at - timedelta(hours=1),
                             cutoff=message.published_at + timedelta(hours=1)).model_dump(mode="json")
    poll(24, admission=sweep, mode="CLOSED_SWEEP")
    with bus._connect() as db:
        assert db.execute("SELECT count(*) FROM distribution_deliveries").fetchone()[0] == 5

