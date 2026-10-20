import json

import pytest

from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.persistent_runtime_v2.w3_contracts import OPERATIONS_PHASE, TRADE_PHASE, W3_CONTRACT, W3_LEGACY_CONTRACT
from doxagent.w3_backtest.legacy import LegacyReplayAdapter
from doxagent.w3_backtest.runner import ReplayRunner, workspace_path
from tests.test_runtime_policy_direction_usage import NOW, fixture_case
from tests.test_w3_case_models import ModelWorker
from tests.test_w3_v4 import PhaseAgent


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ReplayWorker(ModelWorker):
    def __init__(self):
        super().__init__()
        self.fail_operations = False
        self.forks = []

    async def run(self, request):
        if request.task_phase == OPERATIONS_PHASE and self.fail_operations:
            raise RuntimeError("operations unavailable")
        job = await super().run(request)
        properties = request.output_schema.get("properties", {})
        value = json.loads(job.final_response)
        if request.task_phase == TRADE_PHASE and "prior_expectation" in properties:
            value = dict(decision="LONG", reason="Offline unified direction",
                         prior_expectation="Trial baseline", expectation_delta="Production")
        elif request.task_phase == OPERATIONS_PHASE and "policy" not in properties:
            value.pop("policy", None)
        return job.model_copy(update=dict(final_response=json.dumps(value)))

    async def register_template(self, _):
        return None

    async def fork_thread(self, request):
        self.forks.append(request)
        return {"thread_id": "offline-child"}

    async def acknowledge_job(self, _):
        return None


def fixture(tmp_path, monkeypatch, contract=W3_LEGACY_CONTRACT):
    case, _ = fixture_case()
    context = PhaseAgent().prepare_case(case)
    monkeypatch.setattr("doxagent.w3_backtest.runner.parse_document2", lambda _: context.document2)
    dataset = tmp_path / "dataset"
    case_root = dataset / "inputs" / "C1"
    write(
        case_root / "case.json",
        dict(
            case_key="C1",
            original_case_id=case.case_id,
            evidence_cutoff_at=NOW.isoformat(),
            research_key="research-1",
            original_case=case.model_dump(mode="json"),
        ),
    )
    write(case_root / "historical_state.json", {})
    write(case_root / "evidence" / "index.json", {"availability": "ready"})
    write(case_root / "evidence" / "price_10m.json", {"bars": []})
    research = dataset / "research" / "research-1"
    research.mkdir(parents=True)
    (research / "document1.md").write_text(context.document1, encoding="utf-8")
    (research / "document2.json").write_text(context.document2.model_dump_json(), encoding="utf-8")
    (research / "policy_set.original.json").write_text(
        context.policy_set.model_dump_json(), encoding="utf-8"
    )
    (research / "reference_view.md").write_text(context.reference_view, encoding="utf-8")
    # Gold deliberately lives outside the only directories copied to the workspace.
    write(dataset / "gold.jsonl", {"answer": "SECRET_GOLD"})
    worker = ReplayWorker()
    stores = []

    def worker_factory(root):
        store = LocalWorkspaceStore(workspace_path(root / "workspaces"))
        stores.append(store)
        return worker, LocalWorkspaceClient(store)

    # These recovery fixtures explicitly freeze their business contract, rather
    # than silently following the currently published production prompt pack.
    prompt_root = tmp_path / "prompts"
    (prompt_root / "skills").mkdir(parents=True)
    for name in ("AGENTS.md", "skills/skill.md", "skills/weekend_sweep.md"):
        (prompt_root / name).write_text("Offline fixture instructions", encoding="utf-8")
    write(prompt_root / "contract.json", dict(w3_contract_version=contract))
    runner = ReplayRunner(worker_factory=worker_factory, prompt_root=prompt_root)
    return dataset, runner, worker, stores


def test_replay_uses_production_two_stages_with_isolated_evidence(tmp_path, monkeypatch):
    dataset, runner, worker, stores = fixture(tmp_path, monkeypatch)
    result = runner.run_case(dataset, "C1", tmp_path / "run")
    assert result["status"] == "SUCCEEDED", result["error"]
    assert len(worker.requests) == 3
    assert len(worker.forks) == 1
    assert result["decision"]["decision"] == "EXPERT_TRADE"
    assert result["host_receipt"]["disposition"] == "READY"
    for request in worker.requests:
        assert request.web_search_mode == "disabled"
        assert request.data_mcp_enabled is False
        assert request.allow_subagents is False
        assert request.cutoff_at == NOW
        assert request.model == "gpt-6.1-sol"
    assert worker.requests[1].thread_id == worker.requests[2].thread_id == "offline-child"
    assert "evidence" not in json.loads(worker.requests[0].prompt.split("\n\n")[-1])
    assert "evidence" in json.loads(worker.requests[1].prompt)
    for path in stores[0].root.rglob("*"):
        if path.is_file():
            assert "SECRET_GOLD" not in path.read_text(encoding="utf-8")
    resumed = runner.run_case(dataset, "C1", tmp_path / "run")
    assert resumed["status"] == "SUCCEEDED"
    assert len(worker.requests) == 3


def test_unified_replay_uses_v5_and_preserves_web_broker_isolation(tmp_path, monkeypatch):
    dataset, runner, worker, _ = fixture(tmp_path, monkeypatch, contract=W3_CONTRACT)
    result = runner.run_case(dataset, "C1", tmp_path / "run")
    assert result["status"] == "SUCCEEDED", result["error"]
    assert result["decision"]["decision"] == "LONG"
    assert all(r.web_search_mode == "disabled" and not r.data_mcp_enabled for r in worker.requests)
    assert "policy" not in worker.requests[-1].output_schema["properties"]


def test_operations_failure_resumes_without_repeating_trade(tmp_path, monkeypatch):
    dataset, runner, worker, _ = fixture(tmp_path, monkeypatch)
    worker.fail_operations = True
    first = runner.run_case(dataset, "C1", tmp_path / "run")
    assert first["status"] == "FAILED"
    assert first["decision"]["decision"] == "EXPERT_TRADE"
    worker.fail_operations = False
    second = runner.run_case(dataset, "C1", tmp_path / "run")
    assert second["status"] == "SUCCEEDED", second["error"]
    assert [r.task_phase for r in worker.requests] == [
        "w3_template",
        "trade_decision",
        "post_decision_operations",
    ]


def test_host_commit_failure_preserves_model_decision_for_scoring_and_resume(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.w3_execution import W3Execution

    dataset, runner, worker, _ = fixture(tmp_path, monkeypatch)
    original = W3Execution._trade_record

    def fail_commit(*_):
        raise RuntimeError("simulated host failure")

    monkeypatch.setattr(W3Execution, "_trade_record", fail_commit)
    first = runner.run_case(dataset, "C1", tmp_path / "run")
    assert first["status"] == "FAILED"
    assert first["decision"]["decision"] == "EXPERT_TRADE"
    assert first["host_receipt"]["status"] != "COMMITTED"
    monkeypatch.setattr(W3Execution, "_trade_record", original)
    second = runner.run_case(dataset, "C1", tmp_path / "run")
    assert second["status"] == "SUCCEEDED", second["error"]
    assert [r.task_phase for r in worker.requests].count("trade_decision") == 1


def test_resume_rejects_changed_evidence(tmp_path, monkeypatch):
    dataset, runner, _, _ = fixture(tmp_path, monkeypatch)
    runner.run_case(dataset, "C1", tmp_path / "run")
    write(dataset / "inputs" / "C1" / "evidence" / "price_10m.json", {"bars": [1]})
    with pytest.raises(ValueError, match="create a new run"):
        runner.run_case(dataset, "C1", tmp_path / "run")


def test_legacy_direction_inherits_without_mutating_research():
    raw = dict(
        ticker="MU",
        policies=[
            dict(
                policy_id="P1",
                decision="LONG",
                activation_conditions=[
                    dict(
                        condition_id="C1",
                        criterion="increase",
                        calibration={"reference_state": "old", "trigger_boundary": "new"},
                    )
                ],
            )
        ],
    )
    view = LegacyReplayAdapter.policy_execution_view(raw)
    condition = view["policies"][0]["activation_conditions"][0]
    assert condition["decision"] == "LONG"
    assert len(condition["condition_revision"]) == 64
    assert "decision" not in raw["policies"][0]["activation_conditions"][0]
    w2 = LegacyReplayAdapter.normalize_w2(
        dict(
            policy_ids=["P1"],
            matched_condition_ids=[dict(policy_id="P1", condition_ids=["C1"])],
            confidence="normal",
            reason="actual hit",
        )
    )
    assert w2["policy_hit"] is True
    assert w2["policy_related"] is False


def test_embedded_sdk_identity_hook_is_awaited_with_request(tmp_path):
    import asyncio

    from doxagent.w3_backtest.runner import _ReplayRuntime

    class Sdk:
        async def start(self, request, cwd):
            self.receipt_callback("thread-1", None)
            self.receipt_callback("thread-1", "turn-1")
            return "handle"

    runtime = _ReplayRuntime(Sdk())
    received = []

    async def capture(request, thread, turn):
        received.append((request, thread, turn))

    runtime.receipt_callback = capture
    assert asyncio.run(runtime.start("request", tmp_path)) == "handle"
    assert received == [("request", "thread-1", None), ("request", "thread-1", "turn-1")]


def test_legacy_empty_global_state_uses_reconstructed_runtime_snapshot(tmp_path, monkeypatch):
    dataset, runner, _, _ = fixture(tmp_path, monkeypatch)
    write(
        dataset / "inputs/C1/historical_state.json", {"global_state": {"watches": [], "pins": []}}
    )
    result = runner.run_case(dataset, "C1", tmp_path / "run")
    assert result["status"] == "SUCCEEDED", result["error"]


def test_prior_intent_keeps_prior_judgement_but_omits_current_fill_state():
    case, _ = fixture_case()
    trade = dict(
        source=case.source.model_dump(mode="json"),
        decision="LONG",
        daily_status="FILLED_TOMORROW",
        w1_result={"result": "OLD", "reference_ids": ["E7"]},
        w2_result={"policy_ids": ["P1"], "confidence": "normal", "reason": "hit"},
        w3_result={
            "novelty": {"result": "OLD", "reference_ids": ["E7"]},
            "policy": {"policy_ids": ["P1"]},
            "expert_trade": None,
        },
    )
    item, match = LegacyReplayAdapter.historical_intent(
        {"trade": trade, "released_at": NOW.isoformat()}
    )
    assert "FILLED_TOMORROW" not in json.dumps(item)
    assert item["w3_decision"]["novelty"]["result"] == "OLD"
    assert item["execution"]["status"] == "UNKNOWN"
    assert match["policies"] == ["P1"]
    assert match["events"] == ['[null,"event","E7"]']
