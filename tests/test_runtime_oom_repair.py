"""Incident regressions: real-time latency, physical ownership and bounded I/O."""

import asyncio
import json
import time
from contextlib import closing, contextmanager
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.startup_admission import StartupAdmission, startup_scope
from doxagent.codex_worker.thread_lifecycle import WorkerThreadLifecycle
from doxagent.codex_worker.w3_ready import W3ReadySlots
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.persistent_runtime_v2.schema import RuntimeEffect
from tests.test_w3_ready_v5 import Runtime
from tests.test_w3_v4 import enqueue, runtime


def test_eight_blocked_background_effects_do_not_queue_realtime(tmp_path, monkeypatch):
    service, _, _, _ = runtime(tmp_path)
    case = enqueue(service)
    release, realtime = Event(), Event()
    entered = []
    original = service.repository.list_effects(case.case_id)[0]
    with service.repository._connect() as db:
        db.execute("DELETE FROM runtime_v2_effects WHERE effect_id=?", (original.effect_id,))
    for i in range(12):
        service.repository.enqueue_effect(
            RuntimeEffect(
                case_id=case.case_id, effect_type="archive_message", idempotency_key=f"bg-{i}"
            )
        )

    def work(effect):
        if effect.effect_type == "route_to_w3":
            realtime.set()
        else:
            entered.append(effect.effect_id)
            release.wait(10)

    monkeypatch.setattr(service, "_execute_effect", work)
    try:
        assert service.dispatch_pending_effects(limit=20) == 8
        until = time.monotonic() + 3
        while len(entered) < 8 and time.monotonic() < until:
            time.sleep(0.005)
        service.repository.enqueue_effect(original)
        assert service.dispatch_pending_effects() == 1
        assert realtime.wait(1), "real-time W3 waited behind background capacity"
        assert len(entered) == 8
    finally:
        release.set()
        service.close()


@pytest.mark.asyncio
async def test_realtime_bypasses_background_boot_and_handshake_even_under_pressure():
    admission = StartupAdmission(
        safety=SimpleNamespace(read=lambda: SimpleNamespace(level="CRITICAL"))
    )
    async with admission.cold.acquire(), admission.handshake:
        with startup_scope(key="case", realtime=True):
            async with asyncio.timeout(0.1):
                async with admission.boot(), admission.handshake_for():
                    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_terminal_unused_release_fences_late_post_but_preserves_accepted_job(
    tmp_path, accepted
):
    physical = Runtime()
    physical._lock = asyncio.Lock()
    released = []

    async def close(thread):
        released.append(thread)

    physical.release_thread = close
    store = JobStore(tmp_path)
    spaces = LocalWorkspaceStore(tmp_path)
    jobs = SimpleNamespace(store=store, _runtime=physical, _startup=asyncio.Semaphore(3))
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, spaces)
    with closing(store.connect()) as db, db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')"
        )
    slots = W3ReadySlots(
        jobs, spaces, SimpleNamespace(w3_ready_luna=0, w3_ready_sol=0), SimpleNamespace(ready={})
    )
    profile = dict(model="gpt-6-luna", provider=None, effort="max")
    await slots.configure(
        dict(parent_thread_id="parent", profile=profile, instructions={"AGENTS.md": "public"})
    )
    request = dict(
        case_id="case-012345678901234567890123",
        record_id="record",
        parent_thread_id="parent",
        profile=profile,
    )
    value = await slots.claim(request)
    await asyncio.gather(*list(slots.tasks.values()))
    with closing(store.connect()) as db:
        thread = db.execute(
            "SELECT thread_id FROM w3_ready_slots WHERE case_id=?", (request["case_id"],)
        ).fetchone()[0]
    assert thread is not None
    physical.pool = [
        SimpleNamespace(
            thread_id=thread,
            healthy=True,
            ready_slot=True,
            process=SimpleNamespace(returncode=None),
        )
    ]
    value = await slots.claim(request)
    assert not physical.pool[0].ready_slot
    if accepted:
        with closing(store.connect()) as db, db:
            db.execute(
                "INSERT INTO jobs(id,status,payload) VALUES('accepted','queued',?)",
                (
                    json.dumps(
                        dict(run_id=value["run_id"], attempt_id=request["case_id"][-24:] + "-trade")
                    ),
                ),
            )
    try:
        result = await slots.release_unused_case(request["case_id"], "record")
        assert result["released"]
        assert bool(released) is not accepted
        with closing(store.connect()) as db:
            error = db.execute(
                "SELECT error FROM w3_ready_slots WHERE case_id=?", (request["case_id"],)
            ).fetchone()[0]
        assert (error == "CASE_PREPARATION_RELEASED") is not accepted
        if not accepted:
            fake = SimpleNamespace(
                task_phase="trade_decision",
                run_id=value["run_id"],
                attempt_id=request["case_id"][-24:] + "-trade",
                model_dump_json=lambda: "{}",
            )
            job = SimpleNamespace(job_id="late", model_dump_json=lambda: "{}")
            with pytest.raises(ValueError, match="W3_CASE_PREPARATION_RELEASED"):
                store.save(job, fake)
    finally:
        await slots.close()
        await jobs.thread_lifecycle.close()


@pytest.mark.asyncio
async def test_sse_revision_closes_read_wait_race_without_loading_job(monkeypatch):
    import doxagent.codex_worker.jobs as module

    manager = object.__new__(WorkerJobManager)
    manager._condition = asyncio.Condition()
    manager._event_revision = 0
    count = [0]

    async def read(function, *args):
        return function(*args)

    monkeypatch.setattr(module, "read_io", read)

    def status(identity):
        return "running" if count[0] == 0 else "succeeded"

    def events(identity, after):
        count[0] += 1
        manager._event_revision += 1  # Completion between status read and wait.
        return []

    manager.store = SimpleNamespace(status=status, events=events)
    async with asyncio.timeout(0.1):
        assert [event async for event in manager.events("one")] == []
    assert count[0] == 2


@pytest.mark.asyncio
async def test_sse_client_resumes_cursor_and_fetches_only_job_events():
    client = HttpCodexWorkerClient("http://worker", "unused")
    seen = []

    def transport(request):
        seen.append(str(request.url))
        body = "data: " + json.dumps(dict(sequence=4, event_type="heartbeat")) + "\n\n"
        body += "data: " + json.dumps(dict(sequence=5, event_type="job.succeeded")) + "\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://worker", transport=httpx.MockTransport(transport)
    )
    try:
        assert await client._wait_job_event("same-job", 3) == 5
    finally:
        await client._client.aclose()
    assert seen == ["http://worker/v1/jobs/same-job/events?after=3"]


def test_checkpoint_native_io_never_owns_writer_and_publishes_gc_header(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    journal.put_task("task", "MU", "MAINTENANCE", {})
    task = journal.claim("task")
    entered = [False]
    transaction = journal.transaction
    encode = journal.content.encode

    @contextmanager
    def tracked(*args, **kwargs):
        write = kwargs.get("write", True)
        with transaction(*args, **kwargs) as db:
            if write:
                entered[0] = True
            try:
                yield db
            finally:
                if write:
                    entered[0] = False

    def checked(value, *args, **kwargs):
        assert not entered[0]
        return encode(value, *args, **kwargs)

    monkeypatch.setattr(journal, "transaction", tracked)
    monkeypatch.setattr(journal.content, "encode", checked)
    journal.checkpoint(
        task,
        frame=dict(
            run_id="run",
            base=dict(artifacts=dict(event_library=dict(root="/events", version=3))),
            large="x" * 100000,
        ),
    )
    with journal.transaction(write=False, expand=False) as db:
        raw = json.loads(db.execute("SELECT receipt FROM runtime_tasks").fetchone()[0])
    assert raw["_gc_owner"] == dict(
        run_id="run", event_library_root="/events", event_library_version=3
    )
    journal.fail(task, RuntimeError("semantic error"))
    assert journal.get_task("task")["status"] == "PENDING"


@pytest.mark.asyncio
async def test_queued_public_preparation_promotes_without_waiting_for_background_owner():
    admission = StartupAdmission()
    bound = [False]
    entered = asyncio.Event()

    async def work():
        with startup_scope(key="public-now-bound", realtime=lambda: bound[0]):
            async with admission.boot(), admission.handshake_for():
                entered.set()

    async with admission.cold.acquire(), admission.handshake:
        task = asyncio.create_task(work())
        await asyncio.sleep(0.01)
        assert not entered.is_set()
        bound[0] = True
        await asyncio.wait_for(task, 0.6)
        assert admission.cold.active  # Promotion must not release the background owner's permit.


def test_gc_yields_before_history_and_uses_only_candidate_metadata(tmp_path, monkeypatch):
    import sqlite3

    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.resource_gc import collect, migrate, referenced

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    root = tmp_path / "runtime_artifacts" / "run" / "events"
    root.mkdir(parents=True)
    control = tmp_path / "control.db"
    with sqlite3.connect(control) as db:
        db.execute("CREATE TABLE activation_revisions(revision_id,ticker,payload)")
        db.execute("CREATE TABLE ticker_active_revision(revision_id)")
    migrate(journal, control)

    def forbidden(*args):
        raise AssertionError("GC decoded historical native payload")

    monkeypatch.setattr(journal.content, "text", forbidden)
    report = collect(journal, control, should_yield=lambda: True)
    assert not report["retired"] and root.exists()
    assert referenced(journal, control, only_root=str(root))[:2] == ({}, set())


@pytest.mark.asyncio
async def test_realtime_bypass_still_protects_same_uncertain_thread(tmp_path):
    from doxagent.codex_worker.capsules import CapsuleRuntime, CapsuleError

    value = CapsuleRuntime(tmp_path)
    value.pool = [SimpleNamespace(thread_id="same", busy=True, cleanup_state="QUARANTINED")]
    with startup_scope(key="case", realtime=True):
        with pytest.raises(CapsuleError, match="original thread owner"):
            await value._reserve("same")


def test_stopping_is_control_recovery_without_spending_case_attempt(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.journal import RuntimeStopping

    service, _, _, _ = runtime(tmp_path)
    case = enqueue(service)
    effect = service.repository.claim_effects(limit=1)[0]

    def stop(_):
        raise RuntimeStopping("draining")

    monkeypatch.setattr(service, "_execute_effect", stop)
    try:
        assert service._process_claimed_effect(effect) == 0
        saved = service.repository.list_effects(case.case_id)[0]
        assert saved.status == "PENDING_RETRY" and saved.attempt_count == 0
        assert service.repository.get_case(case.case_id).status == "PENDING_W3"
    finally:
        service.close()


def test_effect_renewal_waits_on_existing_local_writer_before_opening_sql_writer(
    tmp_path, monkeypatch
):
    from threading import Thread
    from doxagent.control_io import write_lock
    from tests.test_runtime_effect_claim_cost import seeded, add
    from datetime import timedelta

    repo, now = seeded(tmp_path, monkeypatch)
    add(repo, now, "live", age=1, status="RUNNING", lease=now + timedelta(seconds=60))
    with repo._connect() as db:
        db.execute("DELETE FROM runtime_v2_effects WHERE effect_id LIKE 'history-%'")
    effect = repo.list_effects("case")[0]
    started, done = Event(), Event()

    def renew():
        started.set()
        repo.renew_effects((effect,))
        done.set()

    with write_lock(repo.path):
        thread = Thread(target=renew)
        thread.start()
        assert started.wait(1)
        assert not done.wait(0.05)
        # A waiting local heartbeat must not retain the SQLite WAL writer.
        with repo._connect() as db:
            db.execute("BEGIN IMMEDIATE")
    thread.join(2)
    assert done.is_set()


@pytest.mark.asyncio
async def test_phase_terminal_receipts_use_existing_high_priority_writer(tmp_path, monkeypatch):
    from tests.test_persistent_runtime_worker_receipts import _request, _Worker
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.worker_receipts import ReceiptWorker
    import doxagent.persistent_runtime_v2.worker_receipts as module

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    worker = _Worker()
    worker.requests = [None]
    writes = []
    original = module.write_io

    async def observed(path, function, *args, **kwargs):
        if args and args[0] in ("worker_invocations", "worker_receipts"):
            writes.append((args[0], kwargs.get("priority")))
        return await original(path, function, *args, **kwargs)

    monkeypatch.setattr(module, "write_io", observed)
    request = _request("gpt-6-luna", "attempt").model_copy(update={"task_phase": "trade_decision"})
    await ReceiptWorker(worker, journal, "case", case_id="case").run(request)
    assert writes == [("worker_invocations", 0), ("worker_receipts", 0)]


def test_timing_diagnostics_yield_busy_business_writer(tmp_path):
    from threading import Thread
    from doxagent.control_io import write_lock
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.w3_timing import effect_trace

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    entered, leave = Event(), Event()

    def hold():
        with write_lock(journal.path):
            entered.set()
            leave.wait(2)

    thread = Thread(target=hold)
    thread.start()
    assert entered.wait(1)
    effect = RuntimeEffect(case_id="case", effect_type="route_to_w3", idempotency_key="case")
    try:
        started = time.monotonic()
        with effect_trace(effect, journal):
            pass
        assert time.monotonic() - started < 0.2
        assert journal.get("w3_boundary_timing", effect.effect_id + ":0") is None
        assert list((tmp_path / "boundary-timing").glob("*.json"))
    finally:
        leave.set()
        thread.join()


def test_busy_ui_progress_never_blocks_completed_model_consumer(tmp_path):
    from threading import Thread
    from doxagent.control_io import write_lock
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    runner = object.__new__(CodexW3AgentRunner)
    runner._journal = journal
    runner._case_repository = object()
    entered, leave = Event(), Event()

    def hold():
        with write_lock(journal.path):
            entered.set()
            leave.wait(2)

    thread = Thread(target=hold)
    thread.start()
    assert entered.wait(1)
    case = SimpleNamespace(case_id="case", frozen_inputs={})
    job = SimpleNamespace(job_id="job", status="running", execution_phase="RUNNING")
    try:
        started = time.monotonic()
        runner._observe_phase_job(case, "trade_decision", job)
        assert time.monotonic() - started < 0.1
    finally:
        leave.set()
        thread.join()
    runner._observe_phase_job(case, "trade_decision", job)
    assert journal.get("w3_job_state", "case")["worker_job_id"] == "job"


@pytest.mark.asyncio
async def test_120_live_sse_waits_do_not_queue_materialization_transport():
    client = HttpCodexWorkerClient("http://127.0.0.1", "unused")
    release, all_entered = asyncio.Event(), asyncio.Event()
    active = [0]

    async def serve(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            if b"/probe " in header:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            else:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                active[0] += 1
                if active[0] == 120:
                    all_entered.set()
                await release.wait()
                writer.write(b'data: {"sequence":5,"event_type":"job.succeeded"}\n\n')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client._client.base_url = f"http://127.0.0.1:{port}"
    tasks = [asyncio.create_task(client._wait_job_event(str(i), -1)) for i in range(120)]
    try:
        await asyncio.wait_for(all_entered.wait(), 5)
        response = await asyncio.wait_for(client._client.get("/probe"), 1)
        assert response.status_code == 200
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client._client.aclose()
        server.close()
        await server.wait_closed()


def test_ack_eligibility_is_atomic_with_business_commit(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    registry = W3ThreadRegistry(journal)
    registry.begin_stage("case", "trade_decision")
    registry.save_result(
        "case", "trade_decision", {"action": "NO_TRADE"}, thread_id="thread", worker_job_id="job"
    )
    assert journal.get("w3_ack_pending", "job") is None
    with pytest.raises(RuntimeError):
        with journal.transaction() as db:
            registry.commit_stage(db, "case", "trade_decision")
            raise RuntimeError("rollback")
    assert journal.get("w3_ack_pending", "job") is None
    with journal.transaction() as db:
        registry.commit_stage(db, "case", "trade_decision")
    assert journal.get("w3_ack_pending", "job") == {"case_id": "case"}


def test_failed_market_diagnostic_never_blocks_shared_w3_loop(tmp_path):
    from threading import Thread
    from doxagent.control_io import write_lock
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    failed, entered, release, written = Event(), Event(), Event(), Event()

    class Worker:
        async def start_w3_market(self, manifest):
            failed.set()
            raise RuntimeError("market unavailable")

    runner = CodexW3AgentRunner(worker=Worker(), workspace=None, context_provider=None,
                               journal=journal, prompt_root=tmp_path)
    runner.entry_enabled = lambda case: True
    gap = journal.gap

    def save(*args):
        gap(*args)
        written.set()

    journal.gap = save

    def hold():
        with write_lock(journal.path):
            entered.set()
            release.wait(5)

    thread = Thread(target=hold)
    thread.start()
    assert entered.wait(1)
    try:
        runner.queue_market(SimpleNamespace(case_id="case", ticker="MU"))
        assert failed.wait(1)
        # Force the failed request's completion callback before the probe.
        async def probe():
            await asyncio.sleep(0.05)
            return "transport alive"
        assert asyncio.run_coroutine_threadsafe(probe(), runner._loop).result(0.5) == "transport alive"
        assert not written.is_set()
        release.set()
        assert written.wait(2)
        assert journal.gaps("MU")[0]["code"] == "RuntimeError"
    finally:
        release.set()
        thread.join()
        runner.close()


@pytest.mark.asyncio
async def test_completed_ready_claim_bypasses_background_writer_and_slot_lock(tmp_path):
    from doxagent.control_io import write_io

    workspaces = LocalWorkspaceStore(tmp_path)
    jobs = SimpleNamespace(store=JobStore(tmp_path), _runtime=Runtime(), _startup=asyncio.Semaphore(3))
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, workspaces)
    with closing(jobs.store.connect()) as db, db:
        db.execute("INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')")
    slots = W3ReadySlots(jobs, workspaces,
                         SimpleNamespace(w3_ready_luna=0, w3_ready_sol=0), SimpleNamespace(ready={}))
    profile = dict(model="gpt-6-luna", provider=None, effort="max")
    await slots.configure(dict(parent_thread_id="parent", profile=profile,
                               instructions={"AGENTS.md": "public"}))
    request = dict(case_id="case", record_id="record", parent_thread_id="parent", profile=profile)
    entered, release = Event(), Event()

    def hold():
        entered.set()
        release.wait(5)

    owner = None
    try:
        refill = asyncio.Event()
        async def blocked_refill(key):
            await refill.wait()
        slots.fill = blocked_refill
        first = await asyncio.wait_for(slots.claim(request, wait=True), 1)
        assert first['status'] == 'COMPLETED'
        assert jobs._runtime.forks == 1
        owner = asyncio.create_task(write_io(jobs.store.path, hold))
        while not entered.is_set():
            await asyncio.sleep(0.005)
        async with slots.lock:
            results = await asyncio.wait_for(asyncio.gather(*(slots.claim(request) for _ in range(40))), 1)
        assert all(r['thread_id'] == first['thread_id'] for r in results)
        assert jobs._runtime.forks == 1
    finally:
        release.set()
        if owner:
            await owner
        await slots.close()
        await jobs.thread_lifecycle.close()
