import json

import pytest
from pydantic import ValidationError

from doxagent.persistent_runtime_v2.router import route_runtime_case
from doxagent.persistent_runtime_v2.schema import W1NoveltyResult
from doxagent.persistent_runtime_v2.w3_contracts import (
    W3_CONTRACT,
    W3_LEGACY_CONTRACT,
    W3DirectionDecision,
    W3OperationsResultV5,
    asset_contract_version,
    contract_models,
    parse_w3_operations,
    parse_w3_trade_decision,
)
from tests.test_runtime_policy_direction_usage import fixture_case


def direction(action="LONG"):
    return dict(
        decision=action,
        reason="Material revision",
        prior_expectation="Baseline",
        expectation_delta="New evidence",
    )


def old_operations():
    return dict(
        novelty=dict(result="OLD", reference_ids=["E1"], reason="Covered fact"),
        delta_candidates=[],
        watch_actions=[],
        pin_actions=[],
    )


@pytest.mark.parametrize("action", ["LONG", "SHORT", "NO_TRADE"])
def test_direction_has_no_policy_branch(action):
    assert parse_w3_trade_decision(direction(action), version=W3_CONTRACT).decision == action


@pytest.mark.parametrize(
    "change",
    [
        dict(decision="POLICY_TRADE"),
        dict(decision="EXPERT_TRADE"),
        dict(policy_trade=None),
        dict(expert_trade=None),
        dict(policy_hit=False),
        dict(direction="LONG"),
        dict(reason=" "),
        dict(prior_expectation=None),
        dict(expectation_delta=None),
    ],
)
def test_v5_rejects_old_modes_and_invalid_trades(change):
    with pytest.raises(ValidationError):
        W3DirectionDecision.model_validate(direction() | change)


def test_no_trade_does_not_require_fictional_prior():
    W3DirectionDecision.model_validate(
        direction("NO_TRADE") | dict(prior_expectation=None, expectation_delta=None)
    )
    with pytest.raises(ValidationError):
        W3DirectionDecision.model_validate(dict(decision="NO_TRADE", reason="No opportunity"))


def test_new_operations_forbid_policy_and_trade_fields():
    for addition in (dict(policy={}), dict(decision="LONG")):
        with pytest.raises(ValidationError):
            W3OperationsResultV5.model_validate(old_operations() | addition)
    with pytest.raises(ValidationError):
        W3OperationsResultV5.model_validate(
            old_operations() | dict(novelty=dict(result="NEW", reason="New", reference_ids=[]))
        )
    parsed, issues = parse_w3_operations(
        old_operations() | dict(pin_actions=[dict(action="CANCEL", pin_id=None, reason="Invalid")]),
        version=W3_CONTRACT,
    )
    assert parsed.pin_actions == [] and len(issues) == 1
    missing = old_operations()
    missing.pop("watch_actions")
    with pytest.raises(ValueError):
        parse_w3_operations(missing, version=W3_CONTRACT)


def test_prompt_contract_is_explicit_and_old_bundle_stays_usable():
    assert asset_contract_version({}) == W3_LEGACY_CONTRACT
    assert (
        asset_contract_version(
            {"w3/contract.json": json.dumps(dict(w3_contract_version=W3_CONTRACT))}
        )
        == W3_CONTRACT
    )
    assert contract_models(W3_CONTRACT)[0] is W3DirectionDecision
    with pytest.raises(ValueError):
        contract_models("persistent-runtime.w3.future")


def test_bundle_freezes_distinct_schemas_for_both_contracts(tmp_path):
    from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet

    bundles = ExecutionBundles(RuntimeJournal(tmp_path / "bundles.db"))
    prompts = RuntimeV2PromptSet(*["offline"] * 7)
    assets = {"w3/AGENTS.md": "offline", "w3/skills/skill.md": "offline"}
    frozen = [
        bundles.get(
            bundles.publish(
                prompts,
                assets=assets,
                orchestration_revision=3,
                w3_contract_version=version,
                activate=False,
            )
        )["contracts"]
        for version in (W3_LEGACY_CONTRACT, W3_CONTRACT)
    ]
    assert frozen[0]["w3_trade_schema_hash"] != frozen[1]["w3_trade_schema_hash"]
    assert frozen[0]["w3_operations_schema_hash"] != frozen[1]["w3_operations_schema_hash"]
    assert "policy" in frozen[0]["w3_operations_schema"]["properties"]
    assert "policy" not in frozen[1]["w3_operations_schema"]["properties"]


def test_v5_keeps_old_hit_entry_rule_and_low_override():
    case, _ = fixture_case()
    old = W1NoveltyResult(result="OLD", confidence="normal", reference_ids=["E1"], reason="Covered")
    assert (
        route_runtime_case(old, case.w2_final, w3_contract_version=W3_CONTRACT).primary_route.value
        == "ARCHIVE"
    )
    low = old.model_copy(update={"confidence": type(old.confidence).LOW})
    assert (
        route_runtime_case(low, case.w2_final, w3_contract_version=W3_CONTRACT).primary_route.value
        == "W3"
    )


def test_v5_payload_omits_usage_and_tool_reference_for_new_entry():
    from doxagent.persistent_runtime_v2.w3_context import build_case_payload
    from tests.test_w3_v4 import PhaseAgent

    case, _ = fixture_case()
    case.w3_contract_version = W3_CONTRACT
    case.frozen_inputs["w3_contracts"] = {"w3_entry_version": "w3-entry-v5"}
    case.frozen_inputs["policy_usage"] = [{"policy_id": "P1"}]
    payload = build_case_payload(
        case,
        PhaseAgent().prepare_case(case),
        events=[],
        history=[],
        labels={},
        state=dict(watch=[], pins=[]),
    )
    assert not any(key.startswith("policy_usage") for key in payload)
    assert payload["w2"] == case.w2_final.model_dump(mode="json")
