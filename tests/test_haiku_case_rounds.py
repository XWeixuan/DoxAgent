import json
from dataclasses import replace

import httpx
import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.openrouter import (
    RuntimeRecallOpenRouterClient, build_case_schema_request,
)
from doxagent.persistent_runtime_v2.schema import RuntimeModelTurn
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
from tests.test_w2_r1_openrouter import bailian, request, stream_response


def test_case_profile_freezes_four_rounds_only():
    rounds = configured_rounds(DoxAgentSettings(
        _env_file=None, persistent_runtime_v2_api_profile="openrouter_haiku",
    ))
    assert set(rounds) == {"W1:R2", "W1:R3", "W2:R2", "W2:R3"}
    for config in rounds.values():
        assert config["model"] == "anthropic/claude-haiku-5.5"
        assert config["provider_endpoint"] == "google-vertex/global"
        assert config["reasoning_effort"] == "xhigh"
        assert config["timeout_seconds"] == 300
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
