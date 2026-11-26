import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.w3_v6 import policy_projection, scoped_manifest, trade_input
from doxagent.w3_backtest.context import business_payload, execution_profile, validate_market
from doxagent.w3_backtest.decision_runner import runtime_case


def raw_case():
    return dict(
        case_key="A",
        ticker="MU",
        as_of="2026-10-27T16:02:00+00:00",
        message=dict(title="news", body="facts", published_at="2026-10-27T15:00:00Z"),
        w1=dict(result="NEW", confidence="normal", reference_ids=["E1"]),
        w2={},
    )


def policies():
    return [
        dict(
            policy_id="p",
            title="capacity",
            transmission="capacity changes earnings",
            activation_conditions=[
                dict(
                    condition_id="C1",
                    criterion="new capacity",
                    decision="SHORT",
                    calibration=dict(reference_state="prior", trigger_boundary="private"),
                )
            ],
        )
    ]


def test_full_replay_input_matches_live_projection():
    raw = raw_case()
    w2 = dict(
        policy_hit=True,
        policy_related=False,
        policy_ids=["p"],
        hit_condition_ids=[dict(policy_id="p", condition_ids=["C1"])],
        confidence="normal",
        reason="upstream private reason",
    )
    extra = dict(
        w2_assessment=w2,
        policies=policies(),
        w1_events=[dict(event_id="E1", facts=[])],
        pins=dict(limit=2, used=0, items=[]),
        recent_trade=[],
        research_cutoffs={"D1": None},
    )
    case = runtime_case(raw, Path("a"), {}, execution_profile(raw, extra))
    payload = business_payload(raw, case, extra)
    replay = trade_input(case, payload, [])
    live = SimpleNamespace(running_mode="FULL", frozen_inputs={}, w2_final=SimpleNamespace(model_dump=lambda **_: w2))
    assert replay == trade_input(live, payload, [])
    assert replay["message"]["published_at"].startswith("2026-10-27T15:00")
    assert replay["cutoff_at"] == raw["as_of"]
    assert replay["w1"]["events"][0]["event_id"] == "E1"
    assert "reference_ids" not in replay["w1"]
    visible = json.dumps(replay)
    for private in ("private reason", "trigger_boundary", '"policy_id"', '"decision": "SHORT"'):
        assert private not in visible


def test_partial_context_preserves_known_hit_without_inventing_rule():
    raw = raw_case()
    extra = dict(w2_assessment=dict(policy_hit=True, confidence="low", policy_ids=["p"]))
    case = runtime_case(raw, Path("a"), {}, execution_profile(raw, extra))
    visible = trade_input(case, business_payload(raw, case, extra), [])
    assert visible["w2"] == dict(policy_hit=True, confidence="low")
    assert not {"pins", "recent_trade", "policies"} & visible.keys()
    assert "events" not in visible["w1"]
    assert "RECENT_TRADE" not in visible["additional_tags"]
    # The live path still requires all frozen identities/details.
    live = SimpleNamespace(
        frozen_inputs={}, w2_final=SimpleNamespace(model_dump=lambda **_: extra["w2_assessment"])
    )
    with pytest.raises(ValueError, match="HIT identities"):
        policy_projection(live, {})


def test_model_route_override_and_unknown_not_no_hit():
    raw = raw_case()
    assert (
        execution_profile(raw, {"w2_assessment": {"policy_related": True}})["model"]
        == "gpt-6-luna"
    )
    assert execution_profile(raw, {}, "gpt-6-luna", "max")["effort"] == "max"
    case = runtime_case(raw, Path("a"), {}, execution_profile(raw, {}))
    assert "w2" not in trade_input(case, business_payload(raw, case, {}), [])


def test_scope_and_optional_missing_market():
    manifest = scoped_manifest(
        dict(
            mode="OFFLINE_REPLAY",
            components={"d1": "D", "policy": "P"},
            state={"pins": []},
            evidence={"sources/S01.md": "facts"},
        )
    )
    assert manifest["components"] == {"d1": "D"}
    assert manifest["research_scope"] == "d1-d2.v1" and "state" not in manifest
    assert "sources/S01.md" in manifest["evidence"]
    raw = raw_case()
    validate_market(None, raw)
    with pytest.raises(ValueError, match="after Case cutoff"):
        validate_market(
            dict(
                observed_at=raw["as_of"],
                series={"fine": dict(rows=[["2026-10-27T16:00:00+00:00"]])},
            ),
            raw,
        )


def test_duplicate_policy_titles_match_same_stable_names():
    rows = policies()
    rows.append({**rows[0], "policy_id": "q"})
    w2 = dict(policy_hit=False, policy_related=True, related_policy_ids=["q"], confidence="normal")
    from doxagent.persistent_runtime_v2.w3_v6 import project_policy_input

    projected, rendered = project_policy_input(w2, rows)
    assert len({p["title"] for p in rendered}) == 2
    assert projected["policy_matches"]["related"][0]["policy_title"] == rendered[1]["title"]


def test_explicit_empty_and_missing_remain_different():
    raw = raw_case()
    case = runtime_case(raw, Path("a"), {}, execution_profile(raw, {}))
    missing = trade_input(case, business_payload(raw, case, {}), [])
    empty = trade_input(
        case, business_payload(raw, case, {"recent_trade": [], "w1_events": []}), []
    )
    assert "recent_trade" not in missing and empty["recent_trade"] == []
    assert "events" not in missing["w1"] and empty["w1"]["events"] == []


@pytest.mark.parametrize("with_skill", [True, False])
def test_real_phase_runner_accepts_minimal_offline_case(tmp_path, monkeypatch, with_skill):
    """Use the real phase serializer/validator; replace only the model transport."""
    from doxagent.codex_worker.schema import WorkerJob
    from doxagent.w3_backtest import decision_runner
    from tests.test_persistent_runtime_w3 import _Workspace

    class Worker:
        requests = []

        async def run(self, request):
            self.requests.append(request)
            value = (
                {"ready": True}
                if request.task_phase == "w3_template"
                else dict(
                    decision="NO_TRADE",
                    reason="business evidence",
                    prior_expectation=None,
                    expectation_delta=None,
                )
            )
            return WorkerJob(
                job_id=f"job-{len(self.requests)}",
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="succeeded",
                thread_id=request.thread_id or "mother",
                final_response=json.dumps(value),
            )

        async def register_template(self, job):
            pass

        async def acknowledge_job(self, job):
            pass

        async def prepare_thread(self, request):
            return dict(thread_id="child")

        async def prepare_w3_research(self, ticker, components):
            assert components == {}
            return {}

        async def bind_w3_context(self, cid, phase, manifest):
            assert manifest["research_scope"] == "d1-d2.v1"
            assert manifest["market_view"]["status"] == "UNAVAILABLE"
            return dict(context_ref="frozen-local")

    raw = raw_case()
    dataset, prompts = tmp_path / "data", tmp_path / "prompts"
    folder = dataset / "inputs/A"
    folder.mkdir(parents=True)
    (folder / "case.json").write_text(json.dumps(raw), encoding="utf-8")
    prompts.mkdir()
    for name in ("AGENTS.md", "backtest.md"):
        (prompts / name).write_text("frozen rules", encoding="utf-8")
    if with_skill:
        (prompts / "skills").mkdir()
        (prompts / "skills/skill.md").write_text("separate skill text", encoding="utf-8")
    (prompts / "contract.json").write_text(
        '{"w3_contract_version":"persistent-runtime.w3.v6"}',
        encoding="utf-8",
    )
    worker = Worker()
    monkeypatch.setattr(decision_runner, "native_tool_audit", lambda *_: {})
    runner = decision_runner.DecisionReplayRunner(
        dataset,
        tmp_path / "run",
        prompt_root=prompts,
        worker_factory=lambda _: (worker, _Workspace()),
    )
    try:
        result = runner.run_case(dataset, "A", tmp_path / "run")
        assert result["status"] == "SUCCEEDED", result["error"]
        assert [r.task_phase for r in worker.requests] == ["w3_template", "trade_decision"]
        assert ("separate skill text" in worker.requests[0].prompt) is with_skill
        assert ("w3/skills/skill.md" in runner.assets) is with_skill
        task = json.loads(worker.requests[-1].prompt)
        assert task["input"]["cutoff_at"] == raw["as_of"]
        assert not {"as_of", "policies", "pins", "recent_trade"} & task["input"].keys()
        assert worker.requests[-1].w3_entry_version == "w3-entry-v6"
        assert result["input_coverage"]["market"] is False
        assert (
            json.loads((tmp_path / "run/cases/A/visible_input.json").read_text()) == task["input"]
        )
    finally:
        runner.close()
