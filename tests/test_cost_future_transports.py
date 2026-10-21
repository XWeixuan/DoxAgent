import json
from dataclasses import replace

import httpx
import pytest

from doxagent.persistent_runtime_v2.bailian_chat import chat_body
from doxagent.persistent_runtime_v2.future_model_profiles import (
    bailian_glm_rounds,
    vertex_recall_rounds,
)
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
from doxagent.persistent_runtime_v2.vertex import VertexClaudeClient, vertex_body
from tests.test_w2_r1_openrouter import request


def test_glm_sdk_stream_records_native_usage_and_no_hidden_retry():
    from openai import OpenAI

    from doxagent.persistent_runtime_v2.bailian_chat import complete

    calls = []

    def handler(r):
        body = json.loads(r.content)
        calls.append(body)
        assert body["enable_thinking"] is True
        assert body["reasoning_effort"] == "high"
        assert "previous_response_id" not in body
        chunks = [
            {
                "id": "glm-response",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": '{"candidate_policy_ids":["P1"]}'},
                        "finish_reason": None,
                    }
                ],
            },
            {"id": "glm-response", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {
                "id": "glm-response",
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 5,
                    "total_tokens": 105,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="\n\n".join("data: " + json.dumps(c) for c in chunks) + "\n\ndata: [DONE]\n\n",
        )

    client = OpenAI(
        api_key="fake",
        base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    req = replace(
        request(), provider="bailian", model="ZHIPU/GLM-5.3-FlashX", reasoning_effort="high"
    )
    result = complete(client, req)
    assert (
        result.input_tokens,
        result.output_tokens,
        result.cached_input_tokens,
        result.reasoning_tokens,
    ) == (100, 5, 40, 3)
    assert len(calls) == 1


def native_request():
    return replace(
        request(), provider="vertex", model="claude-haiku-5-5", provider_endpoint="global"
    )


def test_vertex_preserves_prefix_and_original_usage_in_a_stream():
    req = native_request()
    body, _ = vertex_body(req)
    assert body["system"][0]["cache_control"]["ttl"] == "1h"
    assert body["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"
    assert "provider" not in body and "response_format" not in body
    assert body["output_config"]["format"]["type"] == "json_schema"
    output = json.dumps({"candidate_policy_ids": ["P1"], "reason": "matched"})
    # Use the model's actual contract; the existing fixture accepts legacy extras only if declared.
    output = req.output_model.model_construct(
        candidate_policy_ids=["P1"], reason="matched"
    ).model_dump_json()
    native = {
        "input_tokens": 100,
        "cache_read_input_tokens": 8000,
        "cache_creation_input_tokens": 2000,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 2000},
        "output_tokens": 0,
    }
    events = [
        {"type": "message_start", "message": {"id": "msg_test", "usage": native}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": output}},
        {
            "type": "message_delta",
            "usage": {**native, "output_tokens": 5},
            "delta": {"stop_reason": "end_turn"},
        },
        {"type": "message_stop"},
    ]

    def handler(r):
        assert "locations/global" in str(r.url)
        assert r.headers["authorization"] == "Bearer fake-token"
        return httpx.Response(
            200,
            content="\n\n".join("data: " + json.dumps(e) for e in events),
            headers={"content-type": "text/event-stream"},
        )

    client = VertexClaudeClient(
        "test-project",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        token_provider=lambda: "fake-token",
    )
    result = client.complete(req)
    assert result.input_tokens == 10100 and result.output_tokens == 5
    assert result.cached_input_tokens == 8000 and result.cache_write_input_tokens == 2000
    assert result.provider_usage["usage"]["input_tokens"] == 100


def test_vertex_validation_error_retains_cost_usage():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "usage": {
                    "input_tokens": 12,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "bad json"}},
        {"type": "message_delta", "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]
    client = VertexClaudeClient(
        "p",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, content="\n\n".join("data: " + json.dumps(e) for e in events)
                )
            )
        ),
        token_provider=lambda: "fake",
    )
    with pytest.raises(RuntimeResponsesError) as error:
        client.complete(native_request())
    assert error.value.usage["input_tokens"] == 12 and error.value.usage["output_tokens"] == 3


def test_glm_explicit_parent_context_and_profiles_stay_opt_in():
    req = replace(
        request(),
        provider="bailian",
        model="ZHIPU/GLM-5.3-FlashX",
        reasoning_effort="high",
        transport="chat_completions_json_object",
        replay_parent=("frozen parent prompt", '{"accepted":true}'),
        inherit_context=False,
        previous_response_id=None,
    )
    body, _ = chat_body(req)
    assert body["response_format"]["type"] == "json_object"
    assert body["extra_body"]["enable_thinking"] is True
    assert body["messages"][1]["content"] == "frozen parent prompt"
    assert "previous_response_id" not in body
    with pytest.raises(RuntimeResponsesError):
        chat_body(
            replace(req, replay_parent=None, previous_response_id="response-from-other-provider")
        )
    profiles = bailian_glm_rounds(
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert set(profiles) == {"W1:R2", "W1:R3", "W2:R2", "W2:R3"}
    assert vertex_recall_rounds()["W2:R1"]["cache_ttl"] == "1h"


def test_profiles_are_explicit_and_default_factory_settings_do_not_switch_models():
    from types import SimpleNamespace

    from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds

    assert configured_rounds(SimpleNamespace(persistent_runtime_v2_api_profile="current")) == {}
    with pytest.raises(ValueError, match="PROJECT"):
        configured_rounds(
            SimpleNamespace(persistent_runtime_v2_api_profile="vertex_recall", vertex_project="")
        )
    configured = configured_rounds(
        SimpleNamespace(
            persistent_runtime_v2_api_profile="vertex_glm",
            vertex_project="test-project",
            bailian_glm_endpoint="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        )
    )
    assert len(configured) == 6
    assert configured["W2:R1"]["provider"] == "vertex"
    assert configured["W2:R3"]["provider"] == "bailian"
