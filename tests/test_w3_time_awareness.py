import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from openai_codex import CodexConfig

from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.schema import W3TimeAwarenessContext, WorkerJob, WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
from doxagent.codex_worker.time_awareness import W3TimeControl, configured_codex
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
from doxagent.persistent_runtime_v2.w3_time_awareness import time_context
from doxagent.settings import DoxAgentSettings
from tests.test_codex_runtime_v2 import _AsyncSdkClient
from tests.test_persistent_runtime_w3 import _Context, _Workspace
from tests.test_runtime_policy_direction_usage import fixture_case
from tests.test_w3_case_models import ModelWorker
from tests.test_w3_thread_lifecycle import request
from tests.test_w3_v4 import PhaseAgent
from tests.test_w3_v5_execution import setup_case


def test_origin_reuses_durable_value_and_memory_fallback_without_writes(tmp_path):
    historical = datetime(2000, 1, 1, tzinfo=UTC)
    path = tmp_path / "runtime.db"
    journal = RuntimeJournal(path, clock=lambda: historical)
    before = datetime.now(UTC)
    first = time_context(journal, "case-1", start=True)
    assert before <= first.started_at_utc <= datetime.now(UTC)
    assert journal.get('w3_time_awareness', 'case-1') is None
    # Durability belongs to the existing business transaction, not this helper.
    journal.set('w3_time_awareness', 'case-1', first.model_dump(mode='json'))
    reopened = RuntimeJournal(path, clock=lambda: historical)
    assert time_context(reopened, "case-1", start=True) == first
    other = time_context(reopened, "case-2", start=True)
    assert other.case_id == "case-2"
    assert other.started_at_utc >= first.started_at_utc


@pytest.mark.parametrize('layer', ['gate', 'sqlite'])
def test_origin_does_not_wait_for_writers_and_keeps_first_memory_value(tmp_path, layer):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    ready, release = threading.Event(), threading.Event()

    def hold():
        if layer == 'gate':
            with journal.transaction():
                ready.set()
                release.wait(5)
        else:
            db = sqlite3.connect(journal.path)
            try:
                db.execute('BEGIN IMMEDIATE')
                ready.set()
                release.wait(5)
            finally:
                db.close()

    holder = threading.Thread(target=hold)
    holder.start()
    assert ready.wait(5)
    try:
        started = time.perf_counter()
        origin = time_context(journal, 'blocked', start=True)
        assert origin is not None
        assert time.perf_counter() - started < 0.25
        assert holder.is_alive()  # The writer is still held; we did not wait.
        assert time_context(journal, 'blocked') == origin
    finally:
        release.set()
        holder.join(5)
    assert time_context(journal, 'blocked', start=True) == origin
    assert journal.get('w3_time_awareness', 'blocked') is None


def test_trade_origin_precedes_preparation_and_is_not_started_for_operations(tmp_path):
    service, journal, agent, case, _ = setup_case(tmp_path)
    order = []

    def begin(case_id):
        order.append("origin")
        return time_context(journal, case_id, start=True)

    def prepare(_case):
        assert time_context(journal, case.case_id) is not None
        assert _case.frozen_inputs['w3_time_awareness']['case_id'] == case.case_id
        order.append("market")

    agent.begin_trade_timing = begin
    agent.queue_market = prepare
    try:
        service.process_pending_effects()
        service.process_pending_effects()
        assert order == ["origin", "market"]
        service._v4_execution().trade(service.repository.get_case(case.case_id))
        assert order == ["origin", "market"]
    finally:
        service.close()


def test_frozen_origin_avoids_all_database_io_and_is_shared_by_branches(tmp_path, monkeypatch):
    journal = RuntimeJournal(tmp_path / 'runtime.db')
    case, _ = fixture_case()
    metadata = W3TimeAwarenessContext(case_id=case.case_id, started_at_utc=datetime.now(UTC))
    case.frozen_inputs['w3_time_awareness'] = metadata.model_dump(mode='json')
    runner = object.__new__(CodexW3AgentRunner)
    runner._journal, runner._time_awareness_enabled = journal, True

    def forbidden(*args, **kwargs):
        raise AssertionError('Elapsed must not do database I/O for a frozen Case')

    monkeypatch.setattr(journal, 'transaction', forbidden)
    assert runner._trade_time_context(case, 'trade_decision') == metadata
    assert runner._trade_time_context(case.model_copy(deep=True), 'trade_decision') == metadata
    assert runner._trade_time_context(case, 'post_decision_operations') is None


@pytest.mark.parametrize('saved', [{'case_id': 'other', 'started_at_utc': 'bad'}, {'bad': 1}])
def test_invalid_origin_and_database_failure_use_one_shared_fallback(tmp_path, monkeypatch, saved):
    journal = RuntimeJournal(tmp_path / 'runtime.db')
    reads = []

    @contextmanager
    def unavailable(**kwargs):
        assert kwargs['write'] is False and kwargs['timeout'] == 0
        reads.append(kwargs)
        raise sqlite3.OperationalError('database is locked')
        yield

    monkeypatch.setattr(journal, 'transaction', unavailable)
    first = time_context(journal, 'case', start=True, saved=saved)
    assert first.case_id == 'case'
    assert time_context(journal, 'case', start=True) == first
    assert len(reads) == 1  # No retry, and subsequent dispatch is memory-only.


@pytest.mark.parametrize("enabled", [False, True])
def test_runner_sends_context_only_on_trade_and_keeps_business_prompt(tmp_path, enabled):
    case, _ = fixture_case()
    case.frozen_inputs["w3_assets"] = {
        "w3/agent.md": "synthetic",
        "w3/skills/w3.md": "synthetic",
        "w3/template.md": "{payload}",
        "w3/task.md": "{payload}",
    }
    worker = ModelWorker()
    journal = RuntimeJournal(tmp_path / "runtime.db")
    runner = CodexW3AgentRunner(
        worker=worker,
        workspace=_Workspace(),
        context_provider=_Context(),
        prompt_root=tmp_path,
        journal=journal,
        time_awareness_enabled=enabled,
        timeout_seconds=30,
    )
    try:
        for phase in ("w3_template", "trade_decision", "post_decision_operations"):
            if phase == "trade_decision":
                runner.begin_trade_timing(case.case_id)
            runner.run_phase(
                case=case,
                record=dict(record_id="record", workspace="w3-test", thread_id="child"),
                phase=phase,
                payload={},
                context=PhaseAgent().prepare_case(case),
            )
        contexts = [r.w3_time_awareness for r in worker.requests]
        assert contexts[0] is None and contexts[2] is None
        assert (contexts[1] is not None) is enabled
        assert all("started_at_utc" not in r.prompt for r in worker.requests)
        assert all("Elapsed:" not in r.prompt for r in worker.requests)
        if enabled:
            assert contexts[1] == runner.begin_trade_timing(case.case_id)
    finally:
        runner.close()


def test_control_scope_and_explicit_binary_configuration(tmp_path):
    settings = DoxAgentSettings(_env_file=None, codex_runtime_bin="default-patched")
    config = configured_codex(CodexConfig(codex_bin="explicit-patched"), settings)
    assert config.codex_bin == "explicit-patched"
    control = W3TimeControl(config, enabled=True)
    metadata = W3TimeAwarenessContext(case_id="case-1", started_at_utc=datetime.now(UTC))
    try:
        base = request().model_copy(update={"w3_time_awareness": metadata})
        for phase in ("w3_template", "post_decision_operations", None):
            control.write(base.model_copy(update={"task_phase": phase}), "child")
            assert json.loads(control.path.read_text()) == {"enabled": False}
        decision = base.model_copy(update={"task_phase": "trade_decision"})
        control.write(decision, "child")
        assert json.loads(control.path.read_text()) == dict(
            enabled=True,
            thread_id="child",
            task_phase="trade_decision",
            **metadata.model_dump(mode="json"),
        )
        control.clear()
        assert json.loads(control.path.read_text()) == {"enabled": False}
        control.write(decision.model_copy(update={"node": "c1"}), "child")
        assert json.loads(control.path.read_text()) == {"enabled": False}
    finally:
        control.close()


@pytest.mark.asyncio
async def test_sdk_control_updates_without_resuming_resident_thread(tmp_path, monkeypatch):
    sdk = _AsyncSdkClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda _: sdk)
    settings = DoxAgentSettings(
        _env_file=None, w3_time_awareness_enabled=True, codex_runtime_bin="patched-test"
    )
    runtime = OpenAICodexRuntime(
        settings=settings, capability_secret="s" * 32, container_isolated=True
    )
    metadata = W3TimeAwarenessContext(case_id="case-1", started_at_utc=datetime.now(UTC))
    base = request().model_copy(
        update=dict(
            task_phase="trade_decision",
            research_assets_mcp_enabled=True,
            w3_entry_version="w3-entry-v6",
            w3_time_awareness=metadata,
        )
    )
    try:
        first = await runtime.start(base, tmp_path)
        assert json.loads(runtime._time_control.path.read_text())["case_id"] == "case-1"
        scope = runtime._resident_thread[1]
        await first.run()
        assert json.loads(runtime._time_control.path.read_text()) == {"enabled": False}
        for phase in ("post_decision_operations", "trade_decision"):
            later = base.model_copy(
                update=dict(
                    task_phase=phase,
                    thread_id=first.thread_id,
                    attempt_id="next-attempt",
                    w3_time_awareness=metadata.model_copy(update={"case_id": "case-2"}),
                )
            )
            handle = await runtime.start(later, tmp_path)
            value = json.loads(runtime._time_control.path.read_text())
            assert value["enabled"] is (phase == "trade_decision")
            assert runtime._resident_thread[1] == scope
            await handle.run()
        assert sdk.thread_resume_id is None
    finally:
        runtime._time_control.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['write', 'context'])
async def test_sdk_elapsed_degradation_still_starts_trade_once(tmp_path, monkeypatch, failure):
    from pathlib import Path

    sdk = _AsyncSdkClient()
    monkeypatch.setattr('doxagent.codex_worker.sdk_runtime.AsyncCodex', lambda _: sdk)
    runtime = OpenAICodexRuntime(
        settings=DoxAgentSettings(
            _env_file=None, w3_time_awareness_enabled=True, codex_runtime_bin='patched-test',
        ),
        capability_secret='s' * 32, container_isolated=True,
    )
    base = request().model_copy(update=dict(
        task_phase='trade_decision', w3_entry_version='w3-entry-v6',
        w3_time_awareness=None if failure == 'context' else W3TimeAwarenessContext(
            case_id='case', started_at_utc=datetime.now(UTC)),
    ))
    attempts = []
    replace = Path.replace

    def fail_control(path, target):
        if target == runtime._time_control.path:
            attempts.append(str(path))
            raise OSError('synthetic control failure')
        return replace(path, target)

    if failure == 'write':
        runtime._time_control.path.write_text('{"enabled":true}')
        monkeypatch.setattr(Path, 'replace', fail_control)
    try:
        handle = await runtime.start(base, tmp_path)
        assert handle.thread_id
        if failure == 'write':
            assert len(attempts) == 1  # No retry during SDK startup.
            assert not runtime._time_control.path.exists()
        else:
            assert json.loads(runtime._time_control.path.read_text()) == {'enabled': False}
        assert (await handle.run()).status == 'completed'
    finally:
        runtime._time_control.close()


def test_missing_patch_disables_optional_control(caplog):
    control = W3TimeControl(CodexConfig(), enabled=True)
    assert control.path is None
    assert "no patched Codex runtime" in caplog.text


def test_control_directory_failure_is_nonfatal(monkeypatch):
    def unavailable(**kwargs):
        raise OSError('synthetic directory failure')

    monkeypatch.delenv('DOXAGENT_MCP_BUDGET_ROOT', raising=False)
    monkeypatch.setattr(
        'doxagent.codex_worker.time_awareness.tempfile.TemporaryDirectory', unavailable,
    )
    control = W3TimeControl(CodexConfig(codex_bin='patched-test'), enabled=True)
    assert control.path is None
    control.write(request(), 'child')
    control.close()


@pytest.mark.asyncio
async def test_pre_upgrade_request_keeps_its_persisted_job_hash(tmp_path):
    original = request(idempotency_key="legacy-job")
    legacy = original.model_dump(mode="json", exclude={"w3_time_awareness"})
    for key in ("task_phase", "web_search_mode"):
        if legacy.get(key) is None:
            legacy.pop(key, None)
    digest = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    job_id = hashlib.sha256(f"{original.run_id}:legacy-job".encode()).hexdigest()
    manager = WorkerJobManager(SimpleNamespace(), LocalWorkspaceStore(tmp_path))
    manager._closed = True  # Reading an existing receipt must not start a dispatcher.
    old_request = WorkerRunRequest.model_validate(legacy)
    manager.store.save(
        WorkerJob(
            job_id=job_id,
            run_id=original.run_id,
            attempt_id=original.attempt_id,
            request_sha256=digest,
            status="succeeded",
        ),
        old_request,
    )
    assert "w3_time_awareness" not in old_request.model_dump(mode="json")
    assert (await manager.submit(old_request)).job_id == job_id
    changed = old_request.model_copy(
        update={
            "w3_time_awareness": W3TimeAwarenessContext(
                case_id="new-case",
                started_at_utc=datetime.now(UTC),
            )
        }
    )
    with pytest.raises(ValueError, match="idempotency key reused"):
        await manager.submit(changed)
