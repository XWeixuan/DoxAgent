import json
from pathlib import Path

from doxagent.persistent_runtime_v2.factory import build_persistent_runtime_v2_service
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
from doxagent.persistent_runtime_v2.w3_context import trading_context
from doxagent.persistent_runtime_v2.w3_contracts import OPERATIONS_PHASE, TRADE_PHASE, W3_CONTRACT
from doxagent.persistent_runtime_v2.w3_models import freeze_case_profile
from doxagent.settings import DoxAgentSettings
from tests.test_persistent_runtime_w3 import _Context, _Workspace
from tests.test_runtime_policy_direction_usage import fixture_case
from tests.test_w3_case_models import ModelWorker
from tests.test_w3_v4 import PhaseAgent, enqueue, runtime


def test_single_skill_loaded_once_with_phase_data_and_no_sweep(tmp_path):
    case, _ = fixture_case()
    case.frozen_inputs["w3_assets"] = {
        "w3/AGENTS.md": "common agents",
        "w3/skills/skill.md": "unique business skill",
    }
    freeze_case_profile(case)
    worker, workspace = ModelWorker(), _Workspace()
    runner = CodexW3AgentRunner(
        worker=worker, workspace=workspace, context_provider=_Context(), prompt_root=tmp_path,
        journal=RuntimeJournal(tmp_path / "r.db"), timeout_seconds=30,
    )
    context = PhaseAgent().prepare_case(case)
    try:
        for phase in ("w3_template", TRADE_PHASE, OPERATIONS_PHASE):
            runner.run_phase(
                case=case, record=dict(record_id="record", workspace="single-skill",
                                      thread_id=None if phase == "w3_template" else "child"),
                phase=phase, payload={"research_path": "context/research/revision"},
                context=context,
            )
            if phase == "w3_template":
                assert not any(p.endswith("reference_view.md") for p in workspace.files)
        mother, trade, operations = worker.requests
        assert mother.prompt.startswith("unique business skill\n\n")
        for request in (trade, operations):
            task = json.loads(request.prompt)
            assert task["phase"] == request.task_phase
            assert task["skill_path"] == "skills/skill.md"
            assert task["research_path"] == "context/research/revision"
            assert "unique business skill" not in request.prompt
            assert "weekend" not in request.prompt
            assert workspace.files["single-skill:" + task["output_schema"]]
        assert json.loads(operations.prompt)["reference_path"].endswith(
            "-trade_decision/reference_view.md"
        )
        assert workspace.files["single-skill:AGENTS.md"] == "common agents"
        assert not any(p.endswith("task.md") or p.endswith("template.md") for p in workspace.files)
    finally:
        runner.close()


def test_factory_publishes_new_contract_from_current_prompt_directory(tmp_path):
    settings = DoxAgentSettings(
        persistent_runtime_v2_enabled=True, persistent_runtime_v2_w3_enabled=True,
        persistent_runtime_v2_storage_mode="sqlite", codex_runtime_storage_mode="sqlite",
        persistent_runtime_v2_remote_projection_enabled=False,
        persistent_runtime_v2_sqlite_path=str(tmp_path / "runtime.db"),
        codex_runtime_sqlite_path=str(tmp_path / "research.db"),
        ticker_initialization_control_path=str(tmp_path / "control.db"),
        event_library_root=str(tmp_path / "events"),
        dashscope_api_key="offline", openrouter_api_key="offline", codex_worker_bearer_token="offline",
        codex_capability_secret="x" * 32,
    )
    service = build_persistent_runtime_v2_service(settings)
    try:
        bundle = service.execution_bundles.get(service.journal.get("execution", "active"))
        r1 = json.loads(bundle["assets"]["runtime_models.json"])["rounds"]["W2:R1"]
        assert r1["model"] == "anthropic/claude-haiku-5.5"
        assert r1["provider"] == "openrouter" and r1["reasoning_effort"] == "high"
        assert r1["provider_endpoint"] == "google-vertex/global" and r1["cache_ttl"] == "1h"
        assert bundle["contracts"]["w3_contract_version"] == W3_CONTRACT
        assert bundle["contracts"]["w3_asset_layout"] == "agents-skill.v1"
        for name in ("AGENTS.md", "skills/skill.md", "skills/weekend_sweep.md"):
            assert bundle["assets"]["w3/" + name] == (
                Path(settings.persistent_runtime_v2_w3_prompt_root) / name
            ).read_text(encoding="utf-8")
    finally:
        service.close()


def test_trading_deadline_is_separate_from_closed_candidate_release():
    case, _ = fixture_case()
    context = trading_context(case)
    assert context["intent_expires_at"] is not None
    case.runtime_mode = "CLOSED"
    context = trading_context(case)
    assert context["output_kind"] == "CANDIDATE"
    assert context["intent_expires_at"] is None
    assert context["holding_exit_at"] is None


def test_operations_distinguishes_control_rejected_closed_candidate(tmp_path, monkeypatch):
    service, _, agent, _ = runtime(tmp_path)
    try:
        case = enqueue(service)
        case.runtime_mode = "CLOSED"
        case.closed_cycle_id = "weekend"
        service.repository.save_case(case)
        monkeypatch.setattr("doxagent.persistent_runtime_v2.trade_output.output_permission",
                            lambda *_: ("ANALYSIS_ONLY", None))
        service.process_pending_effects()
        service.process_pending_effects()
        payload = next(p for _, phase, _, p in agent.calls if phase == OPERATIONS_PHASE)
        assert payload["output_disposition"] == "CANDIDATE"
        assert payload["candidate_eligibility"] == {
            "origin_trade_eligible": False, "trade_disposition": "ANALYSIS_ONLY",
        }
    finally:
        service.close()
