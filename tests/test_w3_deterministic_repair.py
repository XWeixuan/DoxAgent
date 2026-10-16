import asyncio
import json
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.schema import ThreadForkRequest, W3PrepareRequest
from doxagent.codex_worker.thread_lifecycle import WorkerThreadLifecycle
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from tests.test_w3_thread_lifecycle import request


def prepare(key="one", **changes):
    values = dict(
        operation_key="w3-fork:" + key,
        record_id=key,
        source_case_id="case-" + key,
        parent_thread_id="mother",
        run_id="w3-case-" + key,
        attempt_id="trade",
        research_pack_id="a" * 64,
        reference_pack_id="b" * 64,
        case_overlay_sha="c" * 64,
        profile={
            "model": "gpt-6-luna",
            "provider": None,
            "effort": "max",
            "ticker": "MU",
            "data_mcp_enabled": "false",
            "web_search_mode": "disabled",
        },
        cutoff_at="2026-10-16T12:00:00Z",
    )
    values.update(changes)
    return W3PrepareRequest(**values)


def lifecycle(tmp_path, runtime, transport=None):
    store = JobStore(tmp_path)
    jobs = SimpleNamespace(
        store=store,
        _runtime=runtime,
        _startup=asyncio.Semaphore(3),
        _processes_recovered=True,
        generation="generation",
    )
    value = WorkerThreadLifecycle(jobs, LocalWorkspaceStore(tmp_path), transport)
    with store.connect() as db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('mother','TEMPLATE',NULL,'template','ACTIVE')"
        )
        db.commit()
    return value


def test_pack_restart_private_copy_conflict_and_path_safety(tmp_path):
    from doxagent.codex_runtime.errors import AttemptConflict, InvalidWorkspacePath

    store = LocalWorkspaceStore(tmp_path)
    pack = "a" * 64
    store.ensure_research_pack(pack, {"chapter/a.md": "old", "index.json": "{}"})
    store = LocalWorkspaceStore(tmp_path)
    store.ensure_research_pack(pack)
    with pytest.raises(AttemptConflict):
        store.ensure_research_pack(pack, {"chapter/a.md": "different"})
    for run in ("case-a", "case-b"):
        store.materialize_case(run, {"context/research/base": pack}, {"attempts/a/task.json": "{}"})
    first = tmp_path / "case-a/context/research/base/chapter/a.md"
    second = tmp_path / "case-b/context/research/base/chapter/a.md"
    assert not first.is_symlink()
    first.write_text("private change")
    assert second.read_text() == "old"
    assert (tmp_path / ".w3-packs" / pack / "files/chapter/a.md").read_text() == "old"
    store.materialize_case("case-b", {"context/research/base": pack}, {})
    store.ensure_research_pack("b" * 64, {"chapter/a.md": "new"})
    with pytest.raises(AttemptConflict):
        store.materialize_case("case-b", {"context/research/base": "b" * 64}, {})
    with pytest.raises(InvalidWorkspacePath):
        store.ensure_research_pack("c" * 64, {"../outside": "no"})
    with pytest.raises(InvalidWorkspacePath):
        store.materialize_case("case-b", {"../outside": pack}, {})


@pytest.mark.asyncio
async def test_durable_prepare_does_not_wait_for_other_operation_or_submit_model(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Runtime:
        async def prepare_fork(self, req, cwd, parent):
            calls.append(req.run_id)
            if req.run_id.endswith("one"):
                entered.set()
                await release.wait()
            return "child-" + req.run_id

    value = lifecycle(tmp_path, Runtime())
    assert (await value.prepare(prepare()))["status"] == "CREATING"
    await entered.wait()
    await value.prepare(prepare("two"))
    await value.preparations["w3-fork:two"]
    assert value.operation("w3-fork:two")["status"] == "COMPLETED"
    assert value.operation("w3-fork:one")["status"] == "CREATING"
    release.set()
    await asyncio.gather(*list(value.preparations.values()))
    assert (await value.prepare(prepare()))["thread_id"].endswith("one")
    assert len(calls) == 2
    with value.jobs.store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    with pytest.raises(ValueError):
        await value.prepare(prepare(case_overlay_sha="different"))
    await value.close()


@pytest.mark.asyncio
async def test_prepare_lost_fork_response_reconciles_same_child(tmp_path):
    calls = []

    class Runtime:
        async def prepare_fork(self, *_):
            calls.append("fork")
            raise ConnectionError("lost after persist")

    class Transport:
        async def request(self, method, params):
            assert method == "thread/list"
            return {
                "data": [{"id": "persisted-child", "cwd": params["cwd"], "forkedFromId": "mother"}]
            }

        async def close(self):
            pass

    value = lifecycle(tmp_path, Runtime(), Transport())
    await value.prepare(prepare())
    await value.preparations["w3-fork:one"]
    assert value.operation("w3-fork:one")["step"] == "RECONCILING"
    # Restart loses process/task hints; the same durable row is sufficient.
    recovered = WorkerThreadLifecycle(value.jobs, value.workspaces, Transport())
    await recovered.prepare(prepare())
    await recovered.preparations["w3-fork:one"]
    assert recovered.operation("w3-fork:one")["thread_id"] == "persisted-child"
    assert calls == ["fork"]
    await recovered.close()


@pytest.mark.asyncio
async def test_completed_legacy_operation_adapts_without_rewriting_request(tmp_path):
    value = lifecycle(tmp_path, SimpleNamespace())
    spec = prepare()
    legacy = ThreadForkRequest(
        parent_thread_id="mother",
        run_id=spec.run_id,
        idempotency_key=spec.operation_key,
        model="gpt-6-luna",
    )
    canonical = json.dumps(legacy.model_dump(mode="json"), sort_keys=True)
    with value.jobs.store.connect() as db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('old-child','CASE','mother',?,'ACTIVE')",
            (spec.run_id,),
        )
        db.execute(
            "INSERT INTO w3_thread_operations(operation_key,request,thread_id,status) "
            "VALUES(?,?,?,'COMPLETED')",
            (spec.operation_key, canonical, "old-child"),
        )
        db.commit()
    assert (await value.prepare(spec))["thread_id"] == "old-child"
    assert not value.preparations
    with value.jobs.store.connect() as db:
        assert db.execute("SELECT request FROM w3_thread_operations").fetchone()[0] == canonical
    await value.close()


@pytest.mark.asyncio
async def test_sdk_preparation_and_first_turn_share_thread_without_resume(tmp_path):
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from doxagent.settings import DoxAgentSettings

    calls = []

    class Thread:
        id = "child"

        async def turn(self, *args, **kwargs):
            calls.append(("turn", kwargs))
            return SimpleNamespace(id="turn")

    class Client:
        async def thread_fork(self, *args, **kwargs):
            calls.append(("fork", kwargs["config"]))
            return Thread()

        async def thread_resume(self, *args, **kwargs):
            calls.append(("resume", kwargs["config"]))
            return Thread()

        async def close(self):
            pass

    runtime = OpenAICodexRuntime(settings=DoxAgentSettings(codex_capability_secret="s" * 32))
    await runtime._client.close()
    runtime._client = Client()
    req = request(data_mcp_enabled=False).model_copy(
        update={
            "task_phase": "trade_decision",
            "run_id": "w3-case-one",
            "web_search_mode": "disabled",
        }
    )
    cwd = tmp_path / req.run_id
    cwd.mkdir()
    assert runtime._data_contracts is None
    assert await runtime.prepare_fork(req, cwd, "mother") == "child"
    assert len(calls) == 1
    req = req.model_copy(update={"thread_id": "child"})
    await runtime.start(req, cwd)
    assert [c[0] for c in calls] == ["fork", "turn"]
    assert calls[0][1]["web_search"] == "disabled"
    await runtime.start(req.model_copy(update={"attempt_id": "operations"}), cwd)
    assert [c[0] for c in calls][-2:] == ["resume", "turn"]
    await runtime.close()


def test_research_derivation_is_once_per_version_and_survives_restart(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2 import w3_assets
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from tests.test_runtime_policy_direction_usage import fixture_case
    from tests.test_w3_v4 import PhaseAgent

    case, _ = fixture_case()
    context = PhaseAgent().prepare_case(case).model_copy(update={"research_identity": "d" * 64})
    journal = RuntimeJournal(tmp_path / "r.db")
    original = w3_assets.derive_research_assets
    calls = []

    def derive(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(w3_assets, "derive_research_assets", derive)
    first = w3_assets.research_assets(context, journal)
    assert w3_assets.research_assets(context, journal) is first
    w3_assets._cache.clear()
    assert w3_assets.research_assets(context, journal) == first
    assert calls == [1]
    changed = context.model_copy(
        update={"research_identity": "e" * 64, "document1": context.document1 + "\nnew"}
    )
    assert w3_assets.research_assets(changed, journal)[0] != first[0]
    assert len(calls) == 2


def test_research_update_keeps_template_but_instruction_update_replaces_it(tmp_path):
    from tests.test_w3_v4 import enqueue, runtime

    service, journal, agent, _ = runtime(tmp_path, "NO_TRADE")
    original_run, original_prepare = agent.run_phase, agent.prepare_case

    def run(**kwargs):
        result, job = original_run(**kwargs)
        if kwargs["phase"] == "w3_template":
            job = job.model_copy(update={"thread_id": "mother-" + str(len(agent.calls))})
        return result, job

    agent.run_phase = run
    try:
        enqueue(service, "first")
        service.process_pending_effects()
        service.process_pending_effects()
        agent.prepare_case = lambda case: original_prepare(case).model_copy(
            update={"document1": "new research"}
        )
        enqueue(service, "second")
        service.process_pending_effects()
        service.process_pending_effects()
        assert [c[1] for c in agent.calls].count("w3_template") == 1
        case = enqueue(service, "third")
        case.frozen_inputs["w3_assets"]["w3/agent.md"] = "new common instructions"
        service.repository.save_case(case)
        service.process_pending_effects()
        assert [c[1] for c in agent.calls].count("w3_template") == 2
    finally:
        service.close()


def test_failed_case_projection_and_recovery_keep_original_effect_and_thread(tmp_path):
    from doxagent.persistent_runtime_v2.w3_recovery import apply_recovery, inspect_recovery
    from doxagent.v2_read.projectors import DomainProjectors
    from doxagent.v2_read.repository import ReadStore
    from tests.test_w3_v4 import enqueue, runtime

    service, journal, agent, _ = runtime(tmp_path, "NO_TRADE")
    run = agent.run_phase

    def broken(**kwargs):
        if kwargs["phase"] == "trade_decision":
            raise TimeoutError()
        return run(**kwargs)

    agent.run_phase = broken
    try:
        case = enqueue(service)
        for _ in range(3):
            service.process_pending_effects()
        saved = service.repository.get_case(case.case_id)
        assert saved.error_code and "TimeoutError" in saved.error_message
        store = ReadStore(tmp_path / "read.db")
        store.migrate()
        mapper = DomainProjectors(store)
        mapper.source_label = lambda source: dict(
            kind="api", source_id=source["source_id"], binding_id=source["binding_id"], name="test"
        )
        summary = mapper.case(saved.model_dump(mode="json"), 1)[0]["data"]
        assert summary["w3_status"] == "FAILED"
        assert DomainProjectors.case_link(summary)["route_group"] == "FAILED"
        from doxagent.v2_read.graph import project

        records, _ = project(
            store,
            case.ticker,
            summary,
            [
                {
                    "kind": "native:runtime_v2_cases",
                    "id": case.case_id,
                    "data": saved.model_dump(mode="json"),
                }
            ],
        )
        assert any(
            row["kind"] == "graph_member" and row["parent"] == "W3" and row["data"]
            for row in records
        )
        with journal.transaction(write=False) as db:
            record = dict(
                db.execute("SELECT * FROM runtime_v2_w3_threads WHERE kind='CASE'").fetchone()
            )
        operation = dict(
            operation_key="w3-fork:" + record["record_id"],
            status="COMPLETED",
            thread_id=record["thread_id"],
        )
        entry = inspect_recovery(service, case.case_id, record["record_id"], operation)
        apply_recovery(service, entry, operation, notify=False)
        resumed = service.repository.get_case(case.case_id)
        assert resumed.version_pin == case.version_pin
        assert resumed.trade_expired == case.trade_expired
        assert service.repository.list_effects(case.case_id)[0].effect_id == entry["effect_id"]
        forks = agent.forks
        agent.run_phase = run
        assert service.process_pending_effects() == 1
        assert agent.forks == forks
        assert not journal.values("trade_intents")
        with pytest.raises(ValueError):
            apply_recovery(service, entry, operation, notify=False)
    finally:
        service.close()


def test_sdk_import_and_constructor_do_not_load_full_factory():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime; "
                "from doxagent.settings import DoxAgentSettings; "
                "r=OpenAICodexRuntime(settings=DoxAgentSettings(codex_capability_secret='s'*32)); "
                "assert 'doxagent.tools.factory' not in sys.modules; "
                "assert 'doxagent.mcp.data_server' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_warm_w3_jobs_do_not_wait_for_cold_process_launch_ramp(tmp_path):
    import time

    from doxagent.codex_worker.jobs import WorkerJobManager
    from tests.test_worker_resource_budget import Runtime

    runtime = Runtime()
    runtime.can_start_warm = lambda _: True
    manager = WorkerJobManager(runtime, LocalWorkspaceStore(tmp_path), launch_wave_size=1)
    manager._next_launch_at = time.monotonic() + 60
    try:
        for n in range(3):
            req = request().model_copy(
                update={
                    "task_phase": None,
                    "thread_id": f"child-{n}",
                    "run_id": f"warm-{n}",
                    "idempotency_key": f"warm-{n}",
                    "execution_lane": "realtime",
                }
            )
            await manager.submit(req)
        await asyncio.sleep(0.05)
        assert len(manager._active) == 3
        assert manager._next_launch_at > time.monotonic() + 50
    finally:
        await manager.close()


def test_resident_w3_mcp_refreshes_attempt_cutoff_and_observation_scope(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from doxagent.data_runtime.policy import DataCapabilityCodec
    from doxagent.mcp.data_server import DataMcpApplication
    from doxagent.settings import DoxAgentSettings

    codec = DataCapabilityCodec("s" * 32)

    def token(attempt, minute):
        return codec.issue(
            workflow_version="persistent_runtime_w3_v1",
            research_lane="persistent_runtime",
            run_id="w3-run",
            node_id="persistent_runtime_w3",
            agent_role="w3_duty_trading_expert",
            node_attempt_id=attempt,
            ticker="MU",
            cutoff_at=datetime(2026, 10, 16, 12, minute, tzinfo=UTC),
            enabled_tool_ids=["ibkr.market_history"],
        )

    run = tmp_path / "w3-run"
    run.mkdir()
    original = DataMcpApplication(
        claims=codec.verify(token("trade", 0), public_key=codec.public_key),
        run_root=run,
        control_root=tmp_path / ".control/w3-run/trade",
        settings=DoxAgentSettings(),
    )
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"capability": token("operations", 5)}))
    monkeypatch.setenv("DOXAGENT_DATA_MCP_SCOPE_FILE", str(scope))
    monkeypatch.setenv("DOXAGENT_DATA_MCP_PUBLIC_KEY", codec.public_key)
    current = original.refresh_scope()
    assert current.claims.node_attempt_id == "operations"
    assert current.execution._context.node_attempt_id == "operations"
    assert current.execution._context.cutoff_at.minute == 5
    assert original.execution._context.node_attempt_id == "trade"


def test_twelve_case_burst_through_real_worker_api_with_simulated_sdk(tmp_path):
    """Exercise real persistence/API/materialization; no real model or broker."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    from doxagent.codex_runtime.client import HttpCodexWorkerClient
    from doxagent.codex_worker.app import create_worker_app
    from doxagent.codex_worker.sdk_runtime import WorkerTurnResult
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
    from tests.test_runtime_policy_direction_usage import fixture_case
    from tests.test_w3_v4 import PhaseAgent, decision, enqueue, operations, runtime

    class SimulatedSDK:
        def __init__(self):
            self.resident = set()

        def can_start_warm(self, thread):
            return thread in self.resident

        async def prepare_fork(self, req, cwd, parent):
            child = "child-" + uuid4().hex
            self.resident.add(child)
            return child

        async def start(self, req, cwd):
            thread = req.thread_id or "mother-" + uuid4().hex
            result = (
                {"ready": True}
                if req.task_phase == "w3_template"
                else decision("NO_TRADE").model_dump(mode="json")
                if req.task_phase == "trade_decision"
                else operations(watch=False, pin=False).model_dump(mode="json")
            )

            class Handle:
                thread_id = thread
                turn_id = uuid4().hex

                async def run(self):
                    await asyncio.sleep(0.02)
                    return WorkerTurnResult(thread, self.turn_id, "completed", json.dumps(result))

            return Handle()

    from doxagent.w3_backtest.runner import workspace_path

    app = create_worker_app(
        workspace_root=str(workspace_path(tmp_path / "worker")),
        bearer_token="t" * 32,
        capability_secret="s" * 32,
        runtime=SimulatedSDK(),
    )
    client = HttpCodexWorkerClient(
        "http://worker", "t" * 32, capability_secret="s" * 32, poll_seconds=0.01
    )
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://worker",
        headers={"Authorization": "Bearer " + "t" * 32},
    )
    seed, _ = fixture_case()
    context = PhaseAgent().prepare_case(seed)

    class Context:
        async def load(self, case):
            return context.model_copy(
                update={
                    "document2": context.document2.model_copy(update={"ticker": case.ticker}),
                    "policy_set": context.policy_set.model_copy(update={"ticker": case.ticker}),
                }
            )

    service, journal, _, _ = runtime(tmp_path, "NO_TRADE")
    journal.clock = lambda: datetime.now(UTC)
    runner = CodexW3AgentRunner(
        worker=client,
        workspace=client.workspace_view(),
        context_provider=Context(),
        prompt_root=tmp_path,
        journal=journal,
        timeout_seconds=30,
    )
    service._w3_agent = runner
    runner.bind_case_repository(service.repository)

    def admit(name, ticker="MU"):
        case = enqueue(service, name)
        case.source = case.source.model_copy(
            update={"snapshot": case.source.snapshot.model_copy(update={"ticker": ticker})}
        )
        case.frozen_inputs["projection"]["ticker"] = ticker
        case.frozen_inputs["w3_assets"] = {"w3/AGENTS.md": "common", "w3/skills/skill.md": "unique"}
        service.repository.save_case(case)
        return case

    try:
        warmup = admit("warmup")
        assert service.process_pending_effects() == 1, service.repository.list_effects(
            warmup.case_id
        )[0].last_error
        assert service.process_pending_effects() == 1
        admit("warmup-intc", "INTC")
        assert service.process_pending_effects() == 1
        assert service.process_pending_effects() == 1
        cases = [admit("burst-" + str(n), "MU" if n < 6 else "INTC") for n in range(12)]
        effects = service.repository.claim_effects(limit=20)
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=12) as pool:
            assert list(pool.map(service._process_claimed_effect, effects)) == [1] * 12
        elapsed = time.perf_counter() - started
        from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry

        registry = W3ThreadRegistry(journal)
        durations = sorted(
            (
                datetime.fromisoformat(
                    registry.stage(case.case_id, "trade_decision")["committed_at"]
                )
                - effect.created_at
            ).total_seconds()
            - 0.02
            for case, effect in zip(cases, effects, strict=True)
        )
        (tmp_path / "burst-timing.json").write_text(
            json.dumps(
                {
                    "cases": 12,
                    "tickers": 2,
                    "wall_seconds": elapsed,
                    "non_sdk_p90_seconds": durations[10],
                    "sdk": "simulated_20ms",
                    "durations_seconds": durations,
                }
            )
        )
        import os

        if os.name != "nt":
            assert durations[10] < 5
        assert all(
            service.repository.get_case(case.case_id).w3_trade_decision["decision"] == "NO_TRADE"
            for case in cases
        )
        assert not journal.values("trade_intents")
    finally:
        runner._wait_v4(app.state.jobs.close())
        service.close()


def test_effect_wakeups_coalesce_without_waiting_for_ticker_poll(tmp_path):
    from threading import Event

    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal

    entered, release = Event(), Event()
    calls = []

    def pump(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return 0

    coordinator = RuntimeCoordinator(
        SimpleNamespace(dispatch_pending_effects=pump), RuntimeJournal(tmp_path / "r.db")
    )
    try:
        coordinator.wake_effects()
        assert entered.wait(2)
        future = coordinator._effect_future
        for _ in range(100):
            coordinator.wake_effects()
        assert coordinator._effect_future is future
        release.set()
        future.result(timeout=2)
        assert len(calls) == 2
    finally:
        release.set()
        coordinator.close()


def test_public_prewarm_and_case_share_template_key_without_sweep_assets(tmp_path):
    from doxagent.persistent_runtime_v2.w3 import W3ExecutionEnvironment
    from tests.test_w3_v4 import enqueue, runtime

    service, journal, agent, _ = runtime(tmp_path, "NO_TRADE")
    agent.environment = W3ExecutionEnvironment()
    agent.prepare_public_assets = lambda case: None
    original = agent.run_phase

    def run(**kwargs):
        result, job = original(**kwargs)
        return result, job.model_copy(update={"thread_id": "mother-" + str(len(agent.calls))})

    agent.run_phase = run
    assets = {
        "w3/AGENTS.md": "common",
        "w3/skills/skill.md": "unique",
        "w3/skills/weekend_sweep.md": "only for sweep",
        "w3/unused.md": "legacy",
    }
    identity = journal.freeze(dict(compatibility=1, assets=assets))
    journal.set("execution", "active", identity)
    service.input_snapshot_loader = lambda ticker: SimpleNamespace(
        event_library_root=str(tmp_path / "events"),
        document1_run_id="d1",
        document2_run_id="d2",
        index=SimpleNamespace(version=1),
        projection=SimpleNamespace(policy_set_version=1),
    )
    try:
        execution = service._v4_execution()
        execution.prepare_public("MU")
        assert len(agent.calls) == 2
        case = enqueue(service)
        case.frozen_inputs["w3_assets"] = {
            k: v for k, v in assets.items() if k in {"w3/AGENTS.md", "w3/skills/skill.md"}
        }
        case.frozen_inputs["w3_execution_profile"] = dict(
            model="gpt-6-luna", effort="max", provider=None
        )
        execution.registry.begin_stage(case.case_id, "trade_decision")
        execution._thread(case, None, None, [])
        assert len(agent.calls) == 2
    finally:
        service.close()


@pytest.mark.asyncio
async def test_failed_warm_boot_does_not_restart_storm_or_block_requested_launch(
    tmp_path, monkeypatch
):
    from doxagent.codex_worker.capsules import CapsuleRuntime

    calls = []

    class Capsule:
        def __init__(self, root):
            self.busy = True
            self.thread_id = None

        async def launch(self):
            calls.append(1)
            raise RuntimeError("boot failed")

        async def close(self):
            pass

    monkeypatch.setattr("doxagent.codex_worker.capsules.Capsule", Capsule)
    runtime = CapsuleRuntime(tmp_path)
    await runtime.warm(3)
    await asyncio.gather(*list(runtime._warming))
    assert len(calls) == 3
    await runtime.warm(3)
    assert len(calls) == 3
    with pytest.raises(RuntimeError, match="boot failed"):
        await runtime._take(None)
    assert len(calls) == 4
    await runtime.close()


@pytest.mark.asyncio
async def test_http_prepare_lost_response_reconciles_original_operation_immediately():
    import httpx

    from doxagent.codex_runtime.client import HttpCodexWorkerClient

    state = None
    posts = []

    def handle(request):
        nonlocal state
        if request.method == "POST":
            posts.append(json.loads(request.content))
            if len(posts) == 1:
                state = dict(status="CREATING", step="RECONCILING")
                raise httpx.ReadTimeout("response lost", request=request)
            state = dict(status="COMPLETED", step="READY", thread_id="same-child")
        return httpx.Response(200, json=state) if state else httpx.Response(404)

    client = HttpCodexWorkerClient("http://worker", "t" * 32)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://worker"
    )
    try:
        result = await asyncio.wait_for(client.prepare_thread(prepare()), 2)
        assert result["thread_id"] == "same-child"
        assert len(posts) == 2 and posts[0] == posts[1]
    finally:
        await client.aclose()
