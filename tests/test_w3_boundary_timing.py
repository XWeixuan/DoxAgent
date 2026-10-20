import asyncio
import json
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.schema import ThreadForkRequest
from doxagent.codex_worker.thread_lifecycle import AppServerThreads, WorkerThreadLifecycle
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.w3_timing import BoundaryTrace, effect_trace, file_trace, observed, save_trace, span
from tests.test_w3_v4 import enqueue, runtime


def test_trace_crosses_async_and_thread_boundaries_without_content():
    @observed("sync")
    def child():
        with span("leaf", bytes=5, files=1):
            pass

    @observed("async")
    async def work():
        await asyncio.to_thread(child)

    trace = BoundaryTrace(case_id="test")
    with trace.activate():
        asyncio.run(work())
    result = trace.snapshot()
    assert set(result["totals"]) == {"sync", "async", "leaf"}
    events = {v["name"]: v for v in result["events"]}
    assert events["leaf"]["parent_span_id"] == events["sync"]["span_id"]
    assert events["sync"]["parent_span_id"] == events["async"]["span_id"]
    assert result["totals"]["leaf"]["bytes"] == 5
    assert events["async"]["elapsed_ms"] >= events["sync"]["elapsed_ms"]


def test_error_and_sink_failure_do_not_replace_business_exception(tmp_path, monkeypatch):
    def broken(*_):
        raise OSError("secret must not appear in receipt")

    from datetime import UTC, datetime

    effect = SimpleNamespace(
        case_id="c",
        effect_id="e",
        effect_type="route_to_w3",
        lease_token=1,
        created_at=datetime.now(UTC),
        available_at=datetime.now(UTC),
        attempt_count=0,
    )
    with pytest.raises(ValueError, match="business"):
        with effect_trace(effect, SimpleNamespace(set=broken)) as trace:
            with span("failed"):
                raise ValueError("business")
    assert trace.snapshot()["events"][0]["error_type"] == "ValueError"
    assert "business" not in json.dumps(trace.snapshot())
    monkeypatch.setattr("pathlib.Path.write_text", broken)
    save_trace(trace, tmp_path)
    with file_trace(tmp_path, plane="lifecycle"):
        with span("lifecycle.rpc"):
            business_result = "preserved"
    assert business_result == "preserved"


def test_truncation_keeps_complete_file_totals():
    trace = BoundaryTrace()
    with trace.activate():
        for _ in range(300):
            with span("file", bytes=10, files=1):
                pass
    summary = trace.snapshot()
    assert summary["events_truncated"]
    assert len(summary["events"]) == 256
    assert summary["totals"]["file"]["files"] == 300
    assert summary["totals"]["file"]["bytes"] == 3000


def test_timing_sink_does_not_inherit_business_database_lock_wait(tmp_path):
    import sqlite3
    import time

    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.schema import RuntimeEffect

    journal = RuntimeJournal(tmp_path / "timing.sqlite3")
    effect = RuntimeEffect(case_id="c", effect_type="route_to_w3", idempotency_key="test")
    blocker = sqlite3.connect(journal.path)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.perf_counter()
    try:
        with effect_trace(effect, journal):
            business_result = "preserved"
    finally:
        blocker.rollback()
        blocker.close()
    assert business_result == "preserved"
    assert time.perf_counter() - started < 2
    assert list((tmp_path / "boundary-timing").glob("*.json"))


def test_durable_trace_does_not_change_trade_first_semantics(tmp_path):
    service, journal, _, _ = runtime(tmp_path, "NO_TRADE")
    try:
        case = enqueue(service)
        assert service.process_pending_effects() == 1
        with journal.transaction(write=False) as db:
            records = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM runtime_values WHERE namespace='w3_boundary_timing'"
                )
            ]
        assert len(records) == 1
        assert records[0]["case_id"] == case.case_id
        assert "stage.begin" in records[0]["totals"]
        assert "history.membership" in records[0]["totals"]
        assert service.repository.get_case(case.case_id).w3_operations_status != "COMMITTED"
        assert service.dispatch_pending_effects() == 1
        with service._w3_threads_lock:
            threads = list(service._w3_threads)
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert service.repository.get_case(case.case_id).w3_operations_status == "COMMITTED"
        with journal.transaction(write=False) as db:
            receipts = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM runtime_values WHERE namespace='w3_boundary_timing'"
                )
            ]
        dispatched = next(row for row in receipts if "effect_claim_elapsed_ms" in row)
        assert dispatched["effect_claim_batch_size"] == 1
        assert dispatched["effect_claim_elapsed_ms"] >= 0
        assert dispatched["effect_thread_start_ms"] >= 0
    finally:
        service.close()


@pytest.mark.asyncio
async def test_lifecycle_helper_serializes_rpc_without_closing_active_client(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    class Client:
        def __init__(self, *args):
            pass

        async def start(self):
            pass

        async def initialize(self):
            pass

        async def request(self, method, params, response_model):
            if method == "thread/read":
                entered.set()
                await release.wait()
            return response_model.model_validate({})

        async def close(self):
            pass

    monkeypatch.setattr("openai_codex.async_client.AsyncCodexClient", Client)
    transport = AppServerThreads()
    trace = BoundaryTrace()
    with trace.activate():
        first = asyncio.create_task(transport.request("thread/read", {}))
        await entered.wait()
        second = asyncio.create_task(transport.request("thread/fork", {}))
        await asyncio.sleep(0.02)
        assert not second.done()
        release.set()
        await asyncio.gather(first, second)
    rpcs = [v for v in trace.events if v["name"] == "lifecycle.rpc"]
    assert {v["method"] for v in rpcs} == {"thread/read", "thread/fork"}
    await transport.close()


@pytest.mark.asyncio
async def test_caller_timeout_leaves_existing_fork_running_and_recoverable(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    class Transport:
        async def request(self, method, params):
            if method == "thread/list":
                return {"data": []}
            entered.set()
            await release.wait()
            return {"thread": {"id": "late-child"}}

    store = JobStore(tmp_path)
    lifecycle = WorkerThreadLifecycle(
        SimpleNamespace(store=store), LocalWorkspaceStore(tmp_path), Transport()
    )
    with store.connect() as db, db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('mother','TEMPLATE',NULL,"
            "'w3-template-test','ACTIVE')"
        )
    request = ThreadForkRequest(
        parent_thread_id="mother",
        run_id="w3-case-timeout",
        idempotency_key="w3-fork:fixed-identity",
    )
    task = asyncio.create_task(lifecycle.fork(request))
    await entered.wait()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), 0.01)
    assert not task.done()
    release.set()
    assert (await task)["thread_id"] == "late-child"
    assert (await lifecycle.fork(request))["thread_id"] == "late-child"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT status FROM w3_thread_operations").fetchone()[0] == "COMPLETED"
    assert list((tmp_path / "boundary-timing").glob("*.json"))


@pytest.mark.asyncio
async def test_worker_probe_separates_startup_gate_and_runtime_start(tmp_path):
    from doxagent.codex_worker.jobs import WorkerJobManager
    from doxagent.codex_worker.schema import WorkerJob
    from doxagent.codex_worker.sdk_runtime import WorkerTurnResult
    from tests.test_w3_thread_lifecycle import request as w3_request

    class Runtime:
        async def start(self, req, cwd):
            await asyncio.sleep(0.02)
            return SimpleNamespace(run=run, thread_id="child", turn_id="t")

    async def run():
        return WorkerTurnResult("child", "t", "completed", "{}")

    manager = WorkerJobManager(Runtime(), LocalWorkspaceStore(tmp_path))
    req = w3_request()
    manager._jobs["j"] = WorkerJob(
        job_id="j", run_id=req.run_id, attempt_id=req.attempt_id, status="queued"
    )
    entered, release = asyncio.Event(), asyncio.Event()

    class Gate:
        async def acquire(self):
            entered.set()
            await release.wait()

        def release(self):
            pass

    manager._startup = Gate()
    task = asyncio.create_task(manager._run("j", req))
    await entered.wait()
    await asyncio.sleep(0.02)
    release.set()
    await task
    startup = manager._jobs["j"].resource_receipt["startup_timing"]
    assert startup["totals"]["worker.startup_wait"]["elapsed_ms"] >= 15
    assert startup["totals"]["worker.runtime_start"]["elapsed_ms"] >= 15
    assert startup["worker_queue_to_run_ms"] >= 0
    assert manager._pending_finish["j"]["status"] == "succeeded"
    await manager.close()
