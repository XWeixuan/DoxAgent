import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from doxagent.codex_worker.turn_deadline import SdkExecutionTimeout, run_with_deadline
from doxagent.control_io import write_io
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.round_receipt import complete
from doxagent.persistent_runtime_v2.transport import (
    RuntimeResponsesError,
    RuntimeResponsesRequest,
    RuntimeResponsesResult,
)
from doxagent.ticker_initialization.repository import InitializationRepository


def test_freeze_does_not_hold_writer_and_reclaim_keeps_original(tmp_path):
    clock = [datetime.now(UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: clock[0])
    journal.put_task("original", "MU", "CASE", {"source": "x" * 30000})
    calls = []

    def freeze(inputs):
        # This second connection would BUSY/deadlock inside the old claim writer.
        journal.set("probe", "writer_available", True)
        calls.append(1)
        return {**inputs, "frozen_at": clock[0].isoformat()}

    first = journal.claim("original", seconds=1, prepare_inputs=freeze)
    clock[0] += timedelta(seconds=2)
    second = journal.claim("original", seconds=10, prepare_inputs=freeze)
    assert second["inputs"] == first["inputs"]
    assert len(calls) == 1
    assert second["receipt"]["inputs_ready"]


def test_interrupted_freeze_retries_preparation_without_new_identity(tmp_path):
    clock = [datetime.now(UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: clock[0])
    journal.put_task("original", "MU", "CASE", {"cutoff": "original"})

    def fail(_):
        raise OSError("publication interrupted")

    with pytest.raises(OSError):
        journal.claim("original", seconds=1, prepare_inputs=fail)
    clock[0] += timedelta(seconds=2)
    recovered = journal.claim("original", prepare_inputs=lambda v: {**v, "ready": True})
    assert recovered["inputs"] == {"cutoff": "original", "ready": True}
    assert recovered["token"] == 2


def test_latest_ack_is_not_historical_proof_or_liveness(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    with repo._write() as db:
        db.execute("INSERT INTO ticker_active_revision VALUES('MU','R1')")
    assert repo.acknowledge_revision("MU", "R1", "bus")
    with repo._write() as db:
        db.execute("UPDATE activation_worker_ack SET heartbeat=0")
    assert repo.current_revision_admitted("MU", "R1", "bus")
    assert not repo.revision_acknowledged("MU", "R1", "bus")
    with repo._write() as db:
        db.execute("UPDATE ticker_active_revision SET revision_id='R2'")
    assert not repo.current_revision_admitted("MU", "R2", "bus")
    assert repo.acknowledge_revision("MU", "R2", "bus")
    assert not repo.current_revision_admitted("MU", "R1", "bus")


@pytest.mark.asyncio
async def test_write_cancel_retains_physical_owner_and_other_db_progress(tmp_path):
    started, release = threading.Event(), threading.Event()
    order = []

    def slow():
        started.set()
        release.wait(3)
        order.append("first")

    first = asyncio.create_task(write_io(tmp_path / "one.db", slow))
    await asyncio.to_thread(started.wait, 1)
    first.cancel()
    second = asyncio.create_task(write_io(tmp_path / "one.db", lambda: order.append("second")))
    assert await write_io(tmp_path / "two.db", lambda: "independent") == "independent"
    await asyncio.sleep(0.02)
    assert not first.done() and not second.done()
    release.set()
    await asyncio.gather(first, second, return_exceptions=True)
    assert order == ["first", "second"]


class Output(BaseModel):
    verdict: str


def test_paid_completion_reused_after_sql_index_failure(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    calls = []

    def invoke(_):
        calls.append(1)
        return RuntimeResponsesResult(
            Output(verdict="original"), "response-original", 12, 1, 1, None, None
        )

    client = SimpleNamespace(complete=invoke)
    request = RuntimeResponsesRequest("frozen", {"case": "original"}, Output, "output")
    first = complete(journal, client, request, "case:W2:R1:1")
    # Simulate two failures after provider completion, before the SQL turn exists.
    for _ in range(2):
        replay = complete(journal, client, request, "case:W2:R1:1")
        assert replay.response_id == first.response_id
        assert replay.value == first.value
    assert len(calls) == 1
    receipt = next((tmp_path / "runtime-round-receipts").glob("*.json"))
    assert json.loads(receipt.read_text())["status"] == "COMPLETED"


def test_unknown_dispatch_does_not_issue_second_provider_call(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    request = RuntimeResponsesRequest("frozen", {}, Output, "output")
    calls = []

    def interrupted(_):
        calls.append(1)
        raise OSError("connection state unknown")

    with pytest.raises(OSError):
        complete(journal, SimpleNamespace(complete=interrupted), request, "original")
    with pytest.raises(RuntimeResponsesError, match="may still be running"):
        complete(journal, SimpleNamespace(complete=interrupted), request, "original")
    assert calls == [1]


@pytest.mark.asyncio
async def test_sdk_completed_before_deadline_but_result_late_is_accepted():
    class Handle:
        async def run(self):
            self.sdk_completed_monotonic = time.monotonic()
            self.sdk_completed_result = "original completed result"
            await asyncio.sleep(0.06)  # accounting/receipt work is not SDK execution
            return "original completed result"

        async def interrupt(self):
            raise AssertionError("completed SDK must not be interrupted")

    assert await run_with_deadline(Handle(), time.monotonic() + 0.02) == "original completed result"


@pytest.mark.asyncio
async def test_final_text_without_completed_event_still_times_out():
    class Handle:
        interrupted = False

        async def run(self):
            await asyncio.sleep(10)

        async def interrupt(self):
            self.interrupted = True

    handle = Handle()
    with pytest.raises(SdkExecutionTimeout):
        await run_with_deadline(handle, time.monotonic() + 0.02)
    assert handle.interrupted


def test_existing_recovery_does_not_read_ack(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.runtime_scheduler.v2 import V2Scheduler
    from doxagent.v2_control.repository import ControlRepository

    path = tmp_path / "runtime.db"
    SQLitePersistentRuntimeV2Repository(path)
    journal = RuntimeJournal(path)
    journal.put_task("old-case", "MU", "CASE", {})
    seen = []
    scheduler = V2Scheduler.__new__(V2Scheduler)
    scheduler.runtime_v2_service = SimpleNamespace(
        journal=journal, coordinator=SimpleNamespace(tick_existing=seen.append)
    )
    monkeypatch.setattr(ControlRepository, "get", lambda *args: {"analysis_allowed": True})
    scheduler._recover_existing_once()
    assert seen == ["MU"]


def test_wrapper_timeout_reconnects_one_original_future(tmp_path):
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner, W3Error

    journal = RuntimeJournal(tmp_path / "runtime.db")
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    runner = CodexW3AgentRunner.__new__(CodexW3AgentRunner)
    runner._closed = False
    runner._loop, runner._journal = loop, journal
    runner._timeout_seconds = -89.97  # Compress caller wait only; no SDK/model.
    runner._pending_v4, runner._pending_v4_lock = {}, threading.Lock()
    calls = []

    async def original():
        calls.append(1)
        await asyncio.sleep(0.09)
        return "same result"

    try:
        with pytest.raises(W3Error, match="remains owned"):
            runner._wait_v4(original(), key="case:phase")
        time.sleep(0.12)
        assert runner._wait_v4(original(), key="case:phase") == "same result"
        assert calls == [1]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


def test_hot_lanes_inherit_independent_lease_context():
    from concurrent.futures import ThreadPoolExecutor

    from doxagent.persistent_runtime_v2.fencing import _lease
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service

    service = PersistentRuntimeV2Service.__new__(PersistentRuntimeV2Service)
    service._hot_executors_lock = threading.Lock()
    service._hot_executors_closed = False
    service._hot_executors = {"MU": ThreadPoolExecutor(max_workers=2)}
    service._run_hot_lane = lambda case, lane, submitted, run: run()
    service._run_w1_hot = lambda *args: _lease.get()
    service._run_w2_hot = lambda *args: _lease.get()
    token = _lease.set(("task", {"owner": "original"}, None))
    try:
        a, b = service._submit_hot_lanes(SimpleNamespace(ticker="MU"), None, None)
        assert a.result()[1]["owner"] == b.result()[1]["owner"] == "original"
    finally:
        _lease.reset(token)
        service._hot_executors["MU"].shutdown()


def test_runtime_heartbeat_uses_real_status_values():
    from doxagent.runtime_scheduler.schema import TickerRunStatus
    from doxagent.runtime_scheduler.v2 import V2Scheduler

    scheduler = V2Scheduler.__new__(V2Scheduler)
    state = SimpleNamespace(
        status=TickerRunStatus.RUNNING, metadata={"activation_revision_id": "r1"}
    )
    scheduler.repository = SimpleNamespace(get_state=lambda _: state)
    assert scheduler.revision_usable({"ticker": "MU", "revision_id": "r1"})
    state.status = TickerRunStatus.DEGRADED
    assert scheduler.revision_usable({"ticker": "MU", "revision_id": "r1"})
    assert not scheduler.revision_usable({"ticker": "MU", "revision_id": "r2"})


@pytest.mark.asyncio
async def test_ready_claim_sql_contention_does_not_freeze_worker_loop(tmp_path):
    import sqlite3
    from contextlib import closing

    from doxagent.codex_worker.job_store import JobStore
    from doxagent.codex_worker.thread_lifecycle import WorkerThreadLifecycle
    from doxagent.codex_worker.w3_ready import W3ReadySlots
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore

    class Runtime:
        async def prepare_fork(self, request, cwd, parent):
            return "child"

    store = JobStore(tmp_path)
    workspaces = LocalWorkspaceStore(tmp_path)
    jobs = SimpleNamespace(store=store, _runtime=Runtime(), _startup=asyncio.Semaphore(1))
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, workspaces)
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')"
        )
    slots = W3ReadySlots(
        jobs,
        workspaces,
        SimpleNamespace(w3_ready_luna=0, w3_ready_sol=0),
        SimpleNamespace(ready={}),
    )
    profile = dict(model="gpt-6-luna", provider=None, effort="max")
    await slots.configure(
        dict(parent_thread_id="parent", profile=profile, instructions={"AGENTS.md": "contract"})
    )
    lock = sqlite3.connect(store.path, check_same_thread=False)
    lock.execute("BEGIN IMMEDIATE")

    def release():
        time.sleep(0.5)
        lock.rollback()
        lock.close()

    owner = threading.Thread(target=release)
    owner.start()
    try:
        task = asyncio.create_task(
            slots.claim(
                dict(case_id="case", record_id="record", parent_thread_id="parent", profile=profile)
            )
        )
        start = time.monotonic()
        for _ in range(5):
            await asyncio.sleep(0.02)
        assert time.monotonic() - start < 0.4
        result = await task
        assert result["status"] == "CREATING"
        while slots.tasks:
            await asyncio.gather(*list(slots.tasks.values()))
        assert (
            await slots.claim(
                dict(case_id="case", record_id="record", parent_thread_id="parent", profile=profile)
            )
        )["slot_id"] == result["slot_id"]
    finally:
        owner.join()
        await slots.close()
        await jobs.thread_lifecycle.close()
