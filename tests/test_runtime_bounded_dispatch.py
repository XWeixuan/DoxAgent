import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock

from doxagent.persistent_runtime_v2.daily import RuntimeDeltaBatchAdapter
from doxagent.persistent_runtime_v2.schema import (
    RuntimeEffect,
    RuntimeEffectStatus,
    RuntimeFactCandidate,
)
from tests.test_runtime_effect_claim_cost import add, seeded
from tests.test_w3_v4 import enqueue, runtime


def test_dispatch_claims_only_free_capacity_and_leaves_backlog_durable(tmp_path, monkeypatch):
    service, journal, _, _ = runtime(tmp_path)
    service._effect_concurrency = 2
    release = Event()
    started = []
    lock = Lock()
    case = enqueue(service)
    for i in range(4):
        service.repository.enqueue_effect(
            RuntimeEffect(
                case_id=case.case_id, effect_type="archive_message", idempotency_key=f"test-{i}"
            )
        )

    def process(effect, **kwargs):
        with lock:
            started.append(effect.effect_id)
        assert release.wait(5)
        service.repository.save_effect(
            effect.model_copy(update={"status": RuntimeEffectStatus.COMPLETED})
        )

    monkeypatch.setattr(service, "_process_claimed_effect", process)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: service.dispatch_pending_effects(), range(4)))
        assert sum(results) == 2
        assert len(service._active_effect_ids) == 2
        assert len(service._w3_threads) == 2
        statuses = [e.status for e in service.repository.list_effects(case.case_id)]
        assert statuses.count("RUNNING") == 2 and statuses.count("PENDING") == 3
    finally:
        release.set()
        service.close()
    assert not service._active_effect_ids and len(set(started)) == 2


def test_expired_lease_does_not_reclaim_a_live_local_owner(tmp_path, monkeypatch):
    repo, now = seeded(tmp_path, monkeypatch)
    add(repo, now, "live", age=30, status="RUNNING", lease=now - timedelta(seconds=1))
    add(repo, now, "recoverable", age=20, status="RUNNING", lease=now - timedelta(seconds=1))
    add(repo, now, "new", age=10)
    assert [e.effect_id for e in repo.claim_effects(limit=2, exclude_ids=("live",))] == [
        "recoverable",
        "new",
    ]
    assert repo.claim_effects(limit=2, exclude_ids=("live",)) == []
    assert repo.claim_effects(limit=1)[0].effect_id == "live"


def test_thread_start_pressure_defers_without_spending_business_attempts(tmp_path, monkeypatch):
    import doxagent.persistent_runtime_v2.service as module

    service, _, _, _ = runtime(tmp_path)
    case = enqueue(service)
    service.repository.enqueue_effect(
        RuntimeEffect(case_id=case.case_id, effect_type="archive_message", idempotency_key="second")
    )

    def fail_start(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module.Thread, "start", fail_start)
    try:
        assert service.dispatch_pending_effects() == 0
        assert not service._active_effect_ids and not service._w3_threads
        assert all(
            e.status == "PENDING_RETRY" and e.attempt_count == 0
            for e in service.repository.list_effects(case.case_id)
        )
        assert service.repository.get_case(case.case_id).status == "PENDING_W3"
    finally:
        service.close()


def test_heartbeat_start_pressure_is_not_a_case_failure(tmp_path, monkeypatch):
    import doxagent.persistent_runtime_v2.heartbeat as module

    service, _, _, _ = runtime(tmp_path)
    case = enqueue(service)
    effect = service.repository.claim_effects(limit=1)[0]

    def fail_start(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(module.Thread, "start", fail_start)
    try:
        assert service._process_claimed_effect(effect) == 0
        saved = service.repository.list_effects(case.case_id)[0]
        assert saved.status == "PENDING_RETRY" and saved.attempt_count == 0
        assert service.repository.get_case(case.case_id).status == "PENDING_W3"
    finally:
        service.close()


def test_thin_reads_preserve_source_and_o2_without_opening_frozen_pack(tmp_path, monkeypatch):
    service, journal, _, _ = runtime(tmp_path)
    case = enqueue(service)
    case.source.snapshot.body = "large source body " * 3000
    case.frozen_inputs["wide_research"] = "frozen research " * 10000
    case.sweep_id = "sweep-1"
    service.repository.save_case(case)
    with journal.transaction(write=False, expand=False) as db:
        raw = json.loads(db.execute("SELECT payload_json FROM runtime_v2_cases").fetchone()[0])
    frozen_id = raw["frozen_inputs"]["$doxagent_content_v1"]
    read = service.repository.content.files.read_all

    def guarded(identity):
        assert identity != frozen_id, "thin reads must not decode the frozen research pack"
        return read(identity)

    monkeypatch.setattr(service.repository.content.files, "read_all", guarded)
    try:
        assert service.repository.get_case_sweep_id(case.case_id) == "sweep-1"
        assert service.repository.get_case_source(case.source.source_message_id) == case.source
        candidate = service.repository.allocate_provisional(
            ticker=case.ticker,
            trading_date=case.trading_date,
            source_message_id=case.source.source_message_id,
            candidate_index=0,
            candidate=RuntimeFactCandidate(proposition="Actual fact", assertion_state="ACTUAL"),
            published_max_event_numeric_id=1,
        )
        journal.put_task("sweep-1", case.ticker, "SWEEP", {})
        assert service._process_claimed_effect(service.repository.claim_effects(limit=1)[0]) == 0
        thin = RuntimeDeltaBatchAdapter(service.repository)._atomic(candidate)
        monkeypatch.setattr(service.repository, "get_case_source", None)
        monkeypatch.setattr(service.repository, "get_case_by_source", lambda _: case)
        assert RuntimeDeltaBatchAdapter(service.repository)._atomic(candidate) == thin
    finally:
        service.close()
