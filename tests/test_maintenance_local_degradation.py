import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from doxagent.event_library.contracts import (
    CanonicalEventRevision,
    CanonicalFactRevision,
    CanonicalRevisionBundle,
    DeltaResolution,
    EventRetirement,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
    ResidualDeltaResolution,
)
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.message_bus_v2.schema import AcquisitionMode
from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
from doxagent.persistent_runtime_v2.journal import MaintenancePhaseFailure, RuntimeJournal
from doxagent.persistent_runtime_v2.maintenance import RuntimeMaintenance
from tests.test_codex_event_library_incremental import _publish_v1
from tests.test_maintenance_phase_recovery import maintenance_fixture
from tests.test_message_bus_v2 import _bus
from tests.test_persistent_runtime_v2 import _FakeResponses, _source
from tests.test_runtime_orchestration_execution import runtime_at


def _bundle(tmp_path):
    repo = EventLibraryRepository(tmp_path / "library.db")
    _publish_v1(repo, tmp_path)
    service = EventLibraryService(repo)
    snapshot = FrozenRuntimeSnapshot(
        snapshot_id="local-degrade",
        runtime_scope="cdecr:US:MU",
        epoch_id="local-degrade",
        ticker="MU",
        market="US",
        as_of=datetime(2026, 10, 2, 6, tzinfo=UTC),
        atomics=[
            FrozenRuntimeAtomic(
                runtime_atomic_id=f"local-{i}",
                version=1,
                proposition=f"Unique operating fact {i}",
                time="2026-10-01",
                assertion_state="ACTUAL",
                entities=["Micron"],
            )
            for i in range(3)
        ],
    )
    batch = service.delta_compiler.compile(snapshot)
    current = repo.published_events("MU", 1)[0]
    owner = CanonicalEventRevision.model_validate(current.model_dump(mode="json"))
    fact = CanonicalFactRevision.model_validate(current.facts[0].model_dump(mode="json"))
    target = owner.model_copy(
        update={
            "event_id": "T10",
            "facts": [fact.model_copy(update={"consumes_delta_ids": ["D1"]})],
        }
    )
    healthy = owner.model_copy(
        update={
            "event_id": "T11",
            "facts": [fact.model_copy(update={"fact_id": "TF20", "consumes_delta_ids": ["D2"]})],
        }
    )
    missing = owner.model_copy(
        update={"facts": [f for f in owner.facts if f.fact_id != fact.fact_id]}
    )
    bundle = CanonicalRevisionBundle(
        run_id="local-degrade",
        ticker="MU",
        base_library_version=1,
        delta_batch_ids=[batch.batch_id],
        event_revisions=[missing, target, healthy],
        residual_delta_resolutions=[
            ResidualDeltaResolution(
                delta_id="D3",
                resolution="DUPLICATE_FACT",
                target_event_id=owner.event_id,
                target_fact_id=fact.fact_id,
            )
        ],
    )
    return repo, service, bundle, owner, fact


def test_moved_fact_cancels_owner_and_target_but_publishes_healthy_event(tmp_path):
    repo, service, bundle, owner, fact = _bundle(tmp_path)
    bundle = bundle.model_copy(
        update={
            "event_retirements": [
                EventRetirement(
                    event_id=owner.event_id, redirect_to_event_id="T10", reason="SPLIT_TO_SUCCESSOR"
                )
            ]
        }
    )
    publication, result = service.importer.import_and_publish(bundle)
    assert result.status == "PARTIAL"
    assert [e.event_id for e in result.normalized_bundle.event_revisions] == ["T11"]
    assert {
        r.delta_id
        for r in result.normalized_bundle.residual_delta_resolutions
        if r.resolution == DeltaResolution.KEEP_PENDING
    } == {"D1", "D3"}
    assert not result.normalized_bundle.event_retirements
    assert result.pending_delta_count == 2
    restored = repo.get_event("MU", owner.event_id, publication.published_library_version)
    assert restored is not None and fact.fact_id in {f.fact_id for f in restored.facts}
    assert restored == repo.get_event("MU", owner.event_id, 1)
    replay, replay_result = service.importer.import_and_publish(bundle)
    assert replay.published_library_version == publication.published_library_version
    assert replay_result.status == "PARTIAL"
    assert replay_result.normalized_bundle == result.normalized_bundle


@pytest.mark.parametrize("error", ["unknown_fact", "unknown_event", "duplicate_fact"])
def test_other_local_identity_errors_do_not_block_healthy_content(tmp_path, error):
    _, service, bundle, _, _ = _bundle(tmp_path)
    target, healthy = bundle.event_revisions[1:]
    if error == "unknown_fact":
        target = target.model_copy(
            update={"facts": [target.facts[0].model_copy(update={"fact_id": "F99999"})]}
        )
    elif error == "unknown_event":
        target = target.model_copy(update={"event_id": "E99999"})
    else:
        target = target.model_copy(
            update={"facts": [target.facts[0].model_copy(update={"fact_id": "TF21"})]}
        )
        duplicate = target.model_copy(update={"event_id": "T12"})
        bundle = bundle.model_copy(update={"event_revisions": [target, duplicate, healthy]})
    if error != "duplicate_fact":
        bundle = bundle.model_copy(update={"event_revisions": [target, healthy]})
    result = service.validator.validate(bundle)
    assert result.publishable and result.status == "PARTIAL"
    assert [e.event_id for e in result.normalized_bundle.event_revisions] == ["T11"]


def test_all_local_edits_can_be_noop_but_stale_base_remains_global_failure(tmp_path):
    repo, service, bundle, _, _ = _bundle(tmp_path)
    bundle = bundle.model_copy(
        update={"event_revisions": bundle.event_revisions[:2], "residual_delta_resolutions": []}
    )
    publication, result = service.importer.import_and_publish(bundle)
    assert result.publishable and result.pending_delta_count == 3
    assert publication.published_library_version == 1 and repo.published_version("MU") == 1
    assert not service.validator.validate(
        bundle.model_copy(update={"base_library_version": 0, "run_id": "stale-check"})
    ).publishable


@pytest.mark.parametrize("visibility", [None, "2026-09-01", "2026-09-08", "2026-12-01"])
def test_provisional_reader_is_same_case_day_regardless_of_visibility(tmp_path, visibility):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        runtime.responses = _FakeResponses()
        case = runtime.execute_message(_source(), mode="CLOSED", phase="W1")
        runtime.process_pending_effects()
        values = runtime.repository.list_provisional("MU", case.trading_date)
        assert values
        with runtime.repository._connect() as db:
            db.execute("UPDATE runtime_v2_candidates SET trading_date=?", ("2026-09-07",))
            db.commit()
        if visibility:
            journal.set("visibility", "MU", {"day": visibility})
        assert runtime._visible_provisional(case) == []
        assert runtime.repository.visible_provisional("MU", case.trading_date) == []
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True])
async def test_sweep_takes_next_slot_then_realtime_resumes(tmp_path, shared):
    now = [datetime(2026, 9, 14, 12, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
    bus, service = _bus(tmp_path / "bus.db")
    service.start_ticker("MU")
    binding = bus.list_bindings(ticker="MU", active_only=True)[0]
    source = bus.get_source(binding.source_id)
    if shared:
        source = source.model_copy(update={"acquisition_mode": AcquisitionMode.BY_DISTRIBUTION})
    calls = []

    async def poll(*args, **kwargs):
        calls.append("sweep" if kwargs.get("window_cutoff") else "realtime")
        return SimpleNamespace(
            error_code=None,
            enrichment_job_ids=[],
            next_checkpoint={},
            window_done=True,
            window_coverage="COMPLETE",
            poll_run_id="distribution-run",
        )

    scheduler = SimpleNamespace(
        repository=bus,
        service=service,
        _poll=poll,
        _eligible_bindings=lambda *a, **kw: [(source, binding)],
        _initialize_due_slots=lambda *a: None,
        _update_capacity_alerts=lambda *a: None,
        distribution_worker=None,
        _poll_distribution=poll,
        distribution_due=lambda *args: True,
        distribution=SimpleNamespace(
            pending_for_run=lambda *args: [], failures_for_run=lambda *args: []
        ),
    )
    journal.put_task(
        "sweep",
        "MU",
        "SWEEP",
        {"day": "2026-09-14", "cutoff": now[0].isoformat(), "closed_cycle_id": "2026-09-13"},
    )
    owner = BusOrchestration(journal)
    gate = asyncio.Event()
    owner._inflight[binding.binding_id] = asyncio.create_task(gate.wait())
    await owner.run_once(scheduler)
    assert not calls  # Existing realtime request is not cancelled.
    gate.set()
    await asyncio.gather(*owner._inflight.values())
    await owner.run_once(scheduler)
    await asyncio.gather(*owner._inflight.values())
    assert calls == ["sweep"]
    assert journal.tasks(kind="SOURCE_SWEEP")[0]["status"] == "SUCCEEDED"
    await owner.run_once(scheduler)
    await asyncio.gather(*owner._inflight.values())
    assert calls == ["sweep", "realtime"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "error", "with_receipt", "retryable"),
    [
        ("o2-bundle-validate", ValueError("Bundle frozen identity mismatch"), False, False),
        ("o2-publish", OSError("publication storage unavailable"), False, True),
        ("o2-publish", OSError("publication storage unavailable"), True, True),
    ],
)
async def test_replay_and_publish_errors_have_actual_phase(
    tmp_path, monkeypatch, operation, error, with_receipt, retryable
):
    runtime, journal, settings, _ = maintenance_fixture(tmp_path)

    class O2:
        def __init__(self, durable):
            self.operation, self.durable = operation, durable

        async def run(self, **kwargs):
            if with_receipt:
                self.durable.last_identity = "successful-o2-worker"
                journal.set(
                    "worker_receipts",
                    self.durable.last_identity,
                    {"status": "succeeded", "error_code": "STALE_WORKER_CODE"},
                )
            raise error

    def reject_output(*args):
        pytest.fail("Validation/publication must not reject successful model output")

    monkeypatch.setattr(
        "doxagent.persistent_runtime_v2.worker_receipts.ReceiptWorker.reject_output",
        reject_output,
    )

    maintain = RuntimeMaintenance(
        settings,
        runtime,
        journal,
        worker_factory=lambda: SimpleNamespace(),
        o2_factory=lambda _repository, durable: O2(durable),
    )
    try:
        with pytest.raises(MaintenancePhaseFailure) as caught:
            await maintain(journal.claim("maintain"))
        assert caught.value.phase == operation
        assert caught.value.retryable is retryable
        assert caught.value.code == type(error).__name__
        assert caught.value.__cause__ is error
    finally:
        runtime.close()
