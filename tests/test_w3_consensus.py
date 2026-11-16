import itertools
import json
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.w3_consensus import aggregate, freeze_mode
from tests.test_w3_v5_contracts import direction
from tests.test_w3_v5_execution import setup_case


def test_operator_mode_cli_defaults_and_switches(tmp_path):
    from doxagent.persistent_runtime_v2.operations import run_command

    settings = SimpleNamespace(persistent_runtime_v2_sqlite_path=str(tmp_path / "r.db"))

    def command(action, mode=None):
        return run_command(
            SimpleNamespace(command="w3-trade-mode", action=action, mode=mode), settings
        )["mode"]

    assert command("show") == "single"
    assert command("set", "dual_consensus") == "dual_consensus"
    assert command("set", "single") == "single"


@pytest.mark.asyncio
async def test_operations_ignores_explicit_fast_override(tmp_path, monkeypatch):
    from tests import test_w3_ibkr_permissions as checks

    sdk = checks._AsyncSdkClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *a, **k: sdk)
    runtime = checks.OpenAICodexRuntime(
        capability_secret="s" * 32,
        container_isolated=True,
        settings=checks.DoxAgentSettings(ibkr_tws_enabled=False),
        w3_service_tier="fast",
    )
    root = tmp_path / "run"
    root.mkdir()
    await runtime.start(
        checks.WorkerRunRequest(
            workflow_version="persistent_runtime_w3_v1",
            research_lane="persistent_runtime",
            run_id="run",
            ticker="MU",
            node=checks.CodexPersistentRuntimeNode.W3,
            agent_role=checks.CodexPersistentRuntimeAgentRole.W3,
            attempt_id="ops",
            cutoff_at=checks.datetime.now(checks.UTC),
            prompt="offline",
            output_schema={"type": "object"},
            task_phase="post_decision_operations",
            read_only=True,
            data_mcp_enabled=False,
            allow_subagents=False,
            model="gpt-6-luna",
            effort="max",
        ),
        root,
    )
    config = sdk.thread_start_kwargs["config"]
    assert config["service_tier"] == "default" and config["features.fast_mode"] is False


@pytest.mark.parametrize("a,b", list(itertools.product(["LONG", "SHORT", "NO_TRADE"], repeat=2)))
def test_nine_directions(a, b):
    result, meta = aggregate(
        dict(
            A=dict(result=direction(a), duration_ms=10), B=dict(result=direction(b), duration_ms=20)
        )
    )
    assert result["decision"] == (a if a == b else "NO_TRADE")
    assert meta["selected_branch"] == ("A" if a == "NO_TRADE" and b != "NO_TRADE" else "B")
    assert meta["result_source"] == (
        "CONSENSUS_ROUTER" if a != b and "NO_TRADE" not in (a, b) else "MODEL_BRANCH"
    )


def test_freeze_existing_effect_is_single(tmp_path):
    s, j, _, case, _ = setup_case(tmp_path)
    try:
        j.set("w3_trade_config", "mode", "dual_consensus")
        assert freeze_mode(case, j) == "single"
        case.case_id = "new-case"
        case.frozen_inputs.pop("w3_trade_mode")
        assert freeze_mode(case, j) == "dual_consensus"
        j.set("w3_trade_config", "mode", "single")
        assert freeze_mode(case, j) == "dual_consensus"
    finally:
        s.close()


@pytest.mark.parametrize("a,b", [("LONG", "LONG"), ("LONG", "NO_TRADE"), ("LONG", "SHORT")])
def test_pair_commits_once_and_one_operations(tmp_path, a, b):
    s, j, agent, case, _ = setup_case(tmp_path)
    case.frozen_inputs["w3_trade_mode"] = "dual_consensus"
    case.frozen_inputs["large_frozen_regression"] = "x" * 30000
    s.repository.save_case(case)
    calls = []

    def pair(**kwargs):
        for label, record in kwargs["records"].items():
            calls.append(label)
            job = SimpleNamespace(
                job_id="pair-" + label,
                thread_id=record["thread_id"],
                started_at=None,
                finished_at=None,
                final_response=json.dumps(direction(a if label == "A" else b)),
            )
            kwargs["save"](label, direction(a if label == "A" else b), job)

    agent.run_trade_pair = pair
    try:
        assert s.process_pending_effects() == 1
        saved = s.repository.get_case(case.case_id)
        with j.transaction(write=False) as db:
            frozen_raw = db.execute(
                "SELECT json_extract(payload_json,'$.frozen_inputs') FROM runtime_v2_cases WHERE case_id=?",
                (case.case_id,),
            ).fetchone()[0]
        assert set(json.loads(frozen_raw)) == {"$doxagent_content_v1", "bytes"}
        assert saved.w3_trade_decision["decision"] == (a if a == b else "NO_TRADE")
        assert calls == ["A", "B"] and agent.forks == 2
        from doxagent.persistent_runtime_v2.w3_execution import W3Execution

        execution = W3Execution(s)
        execution.trade(saved)
        assert calls == ["A", "B"]
        assert s.process_pending_effects() == 1
        assert s.repository.get_case(case.case_id).w3_operations_status == "COMMITTED"
        assert len(j.values("trade_intents")) == int(a == b and a != "NO_TRADE")
        assert (
            execution.registry.stage(case.case_id, "trade_decision:A")["result_json"]["decision"]
            == a
        )
        assert (
            execution.registry.stage(case.case_id, "trade_decision:B")["result_json"]["decision"]
            == b
        )
    finally:
        s.close()


@pytest.mark.asyncio
async def test_pair_wrapper_starts_b_before_a_finishes(tmp_path):
    import asyncio

    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner

    runner = object.__new__(CodexW3AgentRunner)
    runner._journal = RuntimeJournal(tmp_path / "r.db")
    runner._wait_v4 = lambda coroutine, **kw: coroutine
    both = asyncio.Event()

    async def phase(**kw):
        shared = kw["shared_trade"]
        shared["ready"].set()
        if kw["record"]["id"] == "A":
            await asyncio.wait_for(both.wait(), 1)
        else:
            both.set()
        return {}, SimpleNamespace()

    runner._run_phase = phase
    saved = []
    await runner.run_trade_pair(
        case=SimpleNamespace(case_id="C"),
        records={k: dict(id=k) for k in ("A", "B")},
        payload={},
        context=None,
        research=None,
        save=lambda k, *args: saved.append(k),
    )
    assert sorted(saved) == ["A", "B"]


def test_full_replay_two_real_runner_tasks_have_identical_prompts(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.w3_contracts import W3_CONTRACT
    from tests.test_w3_backtest_runner import fixture

    dataset, runner, worker, _ = fixture(tmp_path, monkeypatch, contract=W3_CONTRACT)
    runner.trade_mode = "dual_consensus"

    async def fork(request):
        return dict(thread_id=request.run_id)

    worker.fork_thread = fork
    result = runner.run_case(dataset, "C1", tmp_path / "run")
    assert result["status"] == "SUCCEEDED", result["error"]
    trades = [r for r in worker.requests if r.task_phase == "trade_decision"]
    assert len(trades) == 2
    assert trades[0].prompt == trades[1].prompt
    assert trades[0].run_id != trades[1].run_id
    assert len([r for r in worker.requests if r.task_phase == "post_decision_operations"]) == 1
    assert result["branches"]["A"]["result_json"]["decision"] == "LONG"


def test_failed_b_never_repeats_saved_a(tmp_path):
    from doxagent.persistent_runtime_v2.w3 import W3Error
    from doxagent.persistent_runtime_v2.w3_execution import W3Execution

    s, j, agent, case, _ = setup_case(tmp_path)
    case.frozen_inputs["w3_trade_mode"] = "dual_consensus"
    case.frozen_inputs["large_frozen_regression"] = "x" * 30000
    s.repository.save_case(case)
    seen = []
    fail = [True]

    def pair(**kwargs):
        for label, record in kwargs["records"].items():
            seen.append(label)
            if label == "B" and fail[0]:
                raise W3Error("provider_failed", "fail B")
            job = SimpleNamespace(
                job_id="job-" + label,
                thread_id=record["thread_id"],
                started_at=None,
                finished_at=None,
                final_response=json.dumps(direction("LONG")),
            )
            kwargs["save"](label, direction("LONG"), job)

    agent.run_trade_pair = pair
    try:
        execution = W3Execution(s)
        with pytest.raises(W3Error):
            execution.trade(case)
        assert not j.values("trade_intents")
        fail[0] = False
        execution.trade(s.repository.get_case(case.case_id))
        assert seen == ["A", "B", "B"]
        assert len(j.values("trade_intents")) == 1
    finally:
        s.close()


def test_saved_aggregate_recovery_keeps_audit_and_does_not_recall(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.w3_execution import W3Execution

    s, _, agent, case, _ = setup_case(tmp_path)
    case.frozen_inputs["w3_trade_mode"] = "dual_consensus"
    case.frozen_inputs["large_frozen_regression"] = "x" * 30000
    s.repository.save_case(case)
    calls = []

    def pair(**kw):
        for label, record in kw["records"].items():
            calls.append(label)
            kw["save"](
                label,
                direction("NO_TRADE"),
                SimpleNamespace(
                    job_id="aggregate-" + label,
                    thread_id=record["thread_id"],
                    started_at=None,
                    finished_at=None,
                    final_response=json.dumps(direction("NO_TRADE")),
                ),
            )

    agent.run_trade_pair = pair
    execution = W3Execution(s)
    original = execution._commit_trade
    monkeypatch.setattr(
        execution,
        "_commit_trade",
        lambda *args: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )
    try:
        with pytest.raises(RuntimeError):
            execution.trade(case)
        monkeypatch.setattr(execution, "_commit_trade", original)
        execution.trade(s.repository.get_case(case.case_id))
        assert calls == ["A", "B"]
        saved = s.repository.get_case(case.case_id)
        assert saved.w3_consensus["selected_branch"] == "A"
        selected = execution.registry.bound(case.case_id)
        with execution.journal.transaction(write=False) as db:
            rows = db.execute("SELECT * FROM runtime_v2_w3_threads WHERE kind='CASE'").fetchall()
        assert len(rows) == 2
        assert all(
            r["owner_case_id"] is None for r in rows if r["record_id"] != selected["record_id"]
        )
    finally:
        s.close()
