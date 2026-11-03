import json
from dataclasses import replace

import httpx
import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.openrouter import (
    RuntimeRecallOpenRouterClient, build_case_schema_request,
)
from doxagent.persistent_runtime_v2.schema import RuntimeModelTurn, W2PolicyAssessmentResultV2
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
from tests.test_w2_r1_openrouter import bailian, request, stream_response
from doxagent.persistent_runtime_v2.case_output_contract import VERSION
from doxagent.persistent_runtime_v2.case_cache import VERSION as CACHE_VERSION


@pytest.mark.parametrize("profile,effort", [("openrouter_haiku", "xhigh"), ("openrouter_haiku_high", "high")])
def test_case_profile_freezes_four_rounds_only(profile, effort):
    rounds = configured_rounds(DoxAgentSettings(
        _env_file=None, persistent_runtime_v2_api_profile=profile,
    ))
    assert set(rounds) == {"W1:R2", "W1:R3", "W2:R2", "W2:R3"}
    for config in rounds.values():
        assert config["model"] == "anthropic/claude-haiku-5.5"
        assert config["provider_endpoint"] == "google-vertex/global"
        assert config["reasoning_effort"] == effort
        assert config["timeout_seconds"] == 300
        assert config["output_contract"] == VERSION
        assert config["case_cache_layout"] == CACHE_VERSION
    assert "xhigh" in RuntimeModelTurn.model_json_schema()["properties"]["reasoning_effort"]["enum"]


def test_native_schema_and_parent_replay_have_no_hidden_provider_state():
    req = replace(request(), metadata={"lane": "W1", "round": "R3"},
                  replay_parent=("frozen R2 input", '{"result":"NEW"}'), reasoning_effort="xhigh")
    body, _ = build_case_schema_request(req)
    assert body["messages"][1:3] == [
        {"role": "user", "content": "frozen R2 input"},
        {"role": "assistant", "content": '{"result":"NEW"}'},
    ]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"]["only"] == ["google-vertex/global"]
    assert body["provider"]["require_parameters"] is True
    assert body["reasoning"] == {"effort": "xhigh"}
    assert body["max_tokens"] == 65536
    assert body["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"
    assert "previous_response_id" not in body
    with pytest.raises(RuntimeResponsesError, match="frozen parent"):
        build_case_schema_request(replace(req, replay_parent=None, inherit_context=True))


@pytest.mark.parametrize("lane,round_name", [("W1", "R2"), ("W1", "R3"), ("W2", "R2"), ("W2", "R3")])
def test_transport_native_schema_routes_all_four_rounds(lane, round_name):
    seen = []
    def handler(r):
        seen.append(json.loads(r.content))
        return stream_response()
    client = RuntimeRecallOpenRouterClient(
        bailian(), api_key="offline",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        result = client.complete(replace(request(), metadata={"lane": lane, "round": round_name}))
        assert result.value.candidate_policy_ids == []
        assert seen[0]["response_format"]["json_schema"]["strict"] is True
    finally:
        client.close()


def test_native_shape_does_not_bypass_policy_identity_invariants():
    """The observed high regression is valid JSON but unusable business state."""
    raw = {"policy_hit": False, "policy_related": True, "policy_ids": [],
           "hit_condition_ids": [], "related_policy_ids": [],
           "confidence": "normal", "reason": "Policy is related, but its identity was omitted"}
    req = replace(request(), output_model=W2PolicyAssessmentResultV2,
                  metadata={"lane": "W2", "round": "R2"})
    client = RuntimeRecallOpenRouterClient(
        bailian(), api_key="offline",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: stream_response(output=raw))),
    )
    try:
        with pytest.raises(RuntimeResponsesError, match="RELATED requires related_policy_ids") as error:
            client.complete(req)
        assert error.value.retryable
        assert json.loads(error.value.usage["raw_output"]) == raw
    finally:
        client.close()


def test_consistency_rules_are_versioned_and_do_not_change_native_schema():
    legacy = replace(request(), output_model=W2PolicyAssessmentResultV2,
                     schema_name="w2_policy_result", metadata={"lane": "W2", "round": "R2"})
    current = replace(legacy, metadata={**legacy.metadata, "output_contract": VERSION})
    before, before_fp = build_case_schema_request(legacy)
    after, after_fp = build_case_schema_request(current)
    assert before["response_format"] == after["response_format"]
    assert before["messages"][1:] == after["messages"][1:]
    old_system = before["messages"][0]["content"][0]["text"]
    new_system = after["messages"][0]["content"][0]["text"]
    assert "Final Output Consistency Checks" not in old_system
    assert new_system.startswith(old_system)
    assert "policy_related 必须等于" in new_system
    assert "不能只在 reason 中写身份" in new_system
    assert before_fp != after_fp


@pytest.mark.parametrize("schema_name", [
    "w1_novelty_result", "w1_fact_extraction_result",
    "w2_policy_result", "w2_novel_fact_alignment_result",
])
def test_fixed_cache_prefix_survives_case_parent_mode_and_repair_changes(schema_name):
    req = replace(request(), schema_name=schema_name,
                  instructions="Fixed round instructions\n\n## Current Capture Mode\n\nNEW_CAPTURE",
                  replay_parent=("first frozen input", "first frozen result"),
                  metadata={"lane": "W1", "round": "R3", "output_contract": VERSION,
                            "case_cache_layout": CACHE_VERSION})
    a, af = build_case_schema_request(req)
    b, bf = build_case_schema_request(replace(
        req, instructions="Fixed round instructions\n\n## Current Capture Mode\n\nAMBIGUOUS_CAPTURE\n\n# Previous Attempt Correction\nprevious output is invalid",
        payload={"source_message": {"title": "different"},
                 "runtime_policy_projection": {"policies": []}},
        replay_parent=("different frozen input", "different frozen result"),
    ))
    assert af == bf and a["session_id"] == b["session_id"]
    assert a["messages"][0]["content"][0] == b["messages"][0]["content"][0]
    assert "NEW_CAPTURE" not in a["messages"][0]["content"][0]["text"]
    assert a["messages"][0] == b["messages"][0]
    assert "AMBIGUOUS_CAPTURE" in b["messages"][-1]["content"]
    assert "previous output is invalid" in b["messages"][-1]["content"]
    assert a["response_format"] == b["response_format"]
    # Exactly the fixed prefix is marked; parent, source and repair data never are.
    assert json.dumps(a).count('"cache_control"') == 1
    assert json.dumps(b).count('"cache_control"') == 1
    assert a["messages"][1:] != b["messages"][1:]
    legacy, _ = build_case_schema_request(replace(req, metadata={"lane": "W1", "round": "R3"}))
    assert "session_id" not in legacy
    assert len(legacy["messages"][0]["content"]) == 1


def test_version_metadata_survives_dispatch_without_changing_turn_receipt_schema():
    from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from doxagent.persistent_runtime_v2.schema import W1NoveltyResult
    from tests.test_persistent_runtime_v2 import _FakeResponses, _FakeKnownEvents, _FakePolicies, _source

    class Responses(_FakeResponses):
        attempts = 0

        def model_settings(self):
            return {"w12_model": self.model, "w12_effort": "medium", "rounds": configured_rounds(
                DoxAgentSettings(_env_file=None, persistent_runtime_v2_api_profile="openrouter_haiku")
            )}

        def complete(self, req):
            if req.output_model is W1NoveltyResult:
                self.attempts += 1
                assert req.metadata["output_contract"] == VERSION
                assert req.metadata["case_cache_layout"] == CACHE_VERSION
                if self.attempts == 1:
                    raise RuntimeResponsesError("structured_output_validation_failed", "fixture", retryable=True)
            return super().complete(req)

    repo = InMemoryPersistentRuntimeV2Repository()
    response = Responses()
    service = PersistentRuntimeV2Service(
        repository=repo, responses=response, known_events=_FakeKnownEvents(), policies=_FakePolicies(),
        retry_delays_seconds=(0, 0), sleep=lambda _: None, dispatch_effects=False,
    )
    try:
        case = service.execute_message(_source())
        turns = [t for t in repo.list_turns(case.case_id) if t.lane == "W1" and t.round_name == "R2"]
        assert [t.status.value for t in turns] == ["PENDING_RETRY", "OK"]
        assert all("output_contract" not in t.model_dump() and "case_cache_layout" not in t.model_dump() for t in turns)
        assert case.frozen_inputs["models"]["rounds"]["W1:R2"]["output_contract"] == VERSION
    finally:
        service.close()
