import asyncio
import json
import time
from concurrent.futures import Future
from datetime import timedelta
from threading import Lock, Thread
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.daily import RuntimeDeltaBatchAdapter
from doxagent.persistent_runtime_v2.schema import (
    RuntimeEffect,
    RuntimeFactCandidate,
)
from tests.test_runtime_effect_claim_cost import add, seeded
from tests.test_w3_v4 import enqueue, runtime


def test_model_waits_do_not_cap_w3_concurrency_or_hold_worker_threads(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner

    service, journal, _, _ = runtime(tmp_path)
    runner = object.__new__(CodexW3AgentRunner)
    runner.environment = SimpleNamespace(mode="PRODUCTION")
    runner._closed = False
    runner._pending_v4 = {}
    runner._pending_v4_lock = Lock()
    runner._timeout_seconds = 5
    runner._loop = asyncio.new_event_loop()
    loop_thread = Thread(target=runner._loop.run_forever)
    loop_thread.start()
    responses = {}
    calls = []
    consumed = []
    lock = Lock()
    case = enqueue(service)
    for i in range(39):
        service.repository.enqueue_effect(
            RuntimeEffect(
                case_id=case.case_id, effect_type="archive_message", idempotency_key=f"test-{i}"
            )
        )
    for effect in service.repository.list_effects(case.case_id):
        responses[effect.effect_id] = Future()

    async def remote(identity):
        with lock:
            calls.append(identity)
        return await asyncio.wrap_future(responses[identity])

    def execute(effect):
        assert runner._wait_v4(remote(effect.effect_id), key=effect.effect_id) == "answer"
        with lock:
            consumed.append(effect.effect_id)

    monkeypatch.setattr(service, "_execute_effect", execute)
    try:
        assert service.dispatch_pending_effects(limit=40) == 40
        deadline = time.monotonic() + 10
        while (
            len(calls) != 40 or len(service._phase_waiters) != 40
        ) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(calls) == 40 and len(service._phase_waiters) == 40
        assert len(service._active_effect_ids) == 40
        assert len(service._effect_executor._threads) <= 32
        assert service.dispatch_pending_effects() == 0
        first = next(iter(responses))
        responses[first].set_result("answer")
        deadline = time.monotonic() + 2
        while first not in consumed and time.monotonic() < deadline:
            time.sleep(0.005)
        assert first in consumed  # Completion continuation has no 5s retry/poll delay.
    finally:
        for response in responses.values():
            if not response.done():
                response.set_result("answer")
        service.close()
        runner._loop.call_soon_threadsafe(runner._loop.stop)
        loop_thread.join()
        runner._loop.close()
    assert not service._active_effect_ids and len(consumed) == 40
    assert len(set(calls)) == len(calls) == 40


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


@pytest.mark.parametrize("phase", ["trade_decision", "post_decision_operations"])
@pytest.mark.parametrize("post_commit_retry", [False, True])
def test_success_resumes_directly_to_commit_without_repreparing(
    tmp_path, monkeypatch, phase, post_commit_retry
):
    import sqlite3

    from doxagent.persistent_runtime_v2.w3 import W3Error
    from doxagent.persistent_runtime_v2.schema import utc_now

    service, journal, agent, _ = runtime(tmp_path, "NO_TRADE")
    case = enqueue(service)
    if phase == "post_decision_operations":
        assert service.process_pending_effects() == 1
    pending = Future()
    prepared = []
    requested = []
    prepare, run = agent.prepare_case, agent.run_phase
    execution = service._v4_execution()
    sync = execution._sync_route_case
    commits = []
    commit = execution.registry.commit_stage

    def count_commit(*args):
        commits.append(args[2])
        return commit(*args)

    def fail_after_commit(*args):
        if (
            post_commit_retry
            and execution.registry.stage(case.case_id, phase)["status"] == "COMMITTED"
        ):
            monkeypatch.setattr(execution, "_sync_route_case", sync)
            raise sqlite3.OperationalError("database is locked")
        return sync(*args)

    monkeypatch.setattr(execution.registry, "commit_stage", count_commit)
    monkeypatch.setattr(execution, "_sync_route_case", fail_after_commit)

    def count_prepare(value):
        prepared.append(value.case_id)
        return prepare(value)

    def defer(**kwargs):
        if kwargs["phase"] != phase:
            return run(**kwargs)
        requested.append(kwargs)
        error = W3Error("w3_pending_external", "in flight")
        error.pending_future = pending
        error.phase_result = pending.result
        raise error

    monkeypatch.setattr(agent, "prepare_case", count_prepare)
    monkeypatch.setattr(agent, "run_phase", defer)
    try:
        assert service.dispatch_pending_effects() == 1
        deadline = time.monotonic() + 5
        while not service._phase_waiters and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(requested) == len(prepared) == 1
        pending.set_result(run(**requested[0]))
        deadline = time.monotonic() + 5
        while service._active_effect_ids and time.monotonic() < deadline:
            time.sleep(0.005)
        if post_commit_retry:
            assert service._effect_continuations
            retry = next(
                e
                for e in service.repository.list_effects(case.case_id)
                if e.status == "PENDING_RETRY"
            )
            with journal.transaction() as db:
                db.execute(
                    "UPDATE runtime_v2_effects SET available_at=? WHERE effect_id=?",
                    (utc_now().isoformat(), retry.effect_id),
                )
            others = tuple(
                e.effect_id
                for e in service.repository.list_effects(case.case_id)
                if e.effect_id != retry.effect_id
            )
            claimed = service.repository.claim_effects(limit=1, exclude_ids=others)
            assert claimed[0].effect_id == retry.effect_id
            assert service._process_claimed_effect(claimed[0]) == 1
        assert service._v4_execution().registry.stage(case.case_id, phase)["status"] == "COMMITTED"
        assert commits == [phase]
        assert len(requested) == len(prepared) == 1
        assert not service._effect_continuations
    finally:
        if not pending.done():
            pending.set_result(run(**requested[0]))
        service.close()


def test_shared_renewal_preserves_token_and_never_revives_an_expired_lease(tmp_path, monkeypatch):
    repo, now = seeded(tmp_path, monkeypatch)
    with repo._connect() as db:
        db.execute("DELETE FROM runtime_v2_effects WHERE effect_id LIKE 'history-%'")
    add(repo, now, "valid", age=20, status="RUNNING", lease=now + timedelta(seconds=30))
    add(repo, now, "expired", age=30, status="RUNNING", lease=now - timedelta(seconds=1))
    add(repo, now, "wrong-token", age=10, status="RUNNING", lease=now + timedelta(seconds=30))
    effects = {e.effect_id: e for e in repo.list_effects("case")}
    repo.renew_effects(
        (
            effects["valid"],
            effects["expired"],
            effects["wrong-token"].model_copy(update={"lease_token": 999}),
        )
    )
    saved = {e.effect_id: e for e in repo.list_effects("case")}
    assert saved["valid"].lease_until == now + timedelta(seconds=120)
    assert saved["valid"].lease_token == effects["valid"].lease_token
    assert saved["expired"] == effects["expired"]
    assert saved["wrong-token"] == effects["wrong-token"]


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
            e.status in {"PENDING", "PENDING_RETRY"} and e.attempt_count == 0
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
