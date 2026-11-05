import hashlib
import json
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


def test_origin_uses_real_time_survives_reopen_and_separates_cases(tmp_path):
    historical = datetime(2000, 1, 1, tzinfo=UTC)
    path = tmp_path / "runtime.db"
    journal = RuntimeJournal(path, clock=lambda: historical)
    before = datetime.now(UTC)
    first = time_context(journal, "case-1", start=True)
    assert before <= first.started_at_utc <= datetime.now(UTC)
    reopened = RuntimeJournal(path, clock=lambda: historical)
    assert time_context(reopened, "case-1", start=True) == first
    other = time_context(reopened, "case-2", start=True)
    assert other.case_id == "case-2"
    assert other.started_at_utc >= first.started_at_utc


def test_optional_origin_does_not_wait_for_database_writer_or_start_late(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    with journal.transaction() as db:
        db.execute("INSERT INTO runtime_values VALUES('test','lock','{}')")
        assert time_context(journal, "blocked", start=True) is None
    # Dispatch only reads the origin; it must not silently start a later clock.
    assert time_context(journal, "blocked") is None


def test_trade_origin_precedes_preparation_and_is_not_started_for_operations(tmp_path):
    service, journal, agent, case, _ = setup_case(tmp_path)
    order = []

    def begin(case_id):
        order.append("origin")
        return time_context(journal, case_id, start=True)

    def prepare(_case):
        assert journal.get("w3_time_awareness", case.case_id) is not None
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
            w3_entry_version="w3-test",
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


def test_missing_patch_disables_optional_control(caplog):
    control = W3TimeControl(CodexConfig(), enabled=True)
    assert control.path is None
    assert "no patched Codex runtime" in caplog.text


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
