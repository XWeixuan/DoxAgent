from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from doxagent.persistent_runtime_v2.bailian_chat import chat_body, complete
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
from tests.test_w2_r1_openrouter import request, stream_response


def test_deepseek_profile_changes_four_stages_only():
    rounds = configured_rounds(
        SimpleNamespace(
            persistent_runtime_v2_api_profile="bailian_deepseek",
            dashscope_chat_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    assert set(rounds) == {"W1:R2", "W1:R3", "W2:R2", "W2:R3"}
    assert all(
        c["model"] == "deepseek-v4.1-flash-1017"
        and c["reasoning_effort"] == "medium"
        and c["provider"] == "bailian"
        for c in rounds.values()
    )


def test_medium_deepseek_preserves_parent_and_strict_schema():
    req = replace(
        request(),
        model="deepseek-v4.1-flash-1017",
        provider="bailian",
        reasoning_effort="medium",
        transport="chat_completions_json_object",
        replay_parent=("original input", '{"accepted":true}'),
    )
    body, _ = chat_body(req)
    assert body["reasoning_effort"] == "medium"
    assert body["messages"][1]["content"] == "original input"
    assert body["response_format"] == {"type": "json_object"}
    client = OpenAI(
        api_key="offline",
        base_url="https://unused.invalid",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r: stream_response())),
    )
    try:
        result = complete(client, req)
        assert result.value.candidate_policy_ids == [] and result.input_tokens == 1000
    finally:
        client.close()


def test_chat_does_not_hide_or_retry_product_permission_error():
    client = OpenAI(
        api_key="offline",
        base_url="https://unused.invalid",
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": "invalid_parameter_error",
                            "message": "The product is not activated",
                        }
                    },
                )
            )
        ),
    )
    try:
        with pytest.raises(RuntimeResponsesError) as caught:
            complete(
                client,
                replace(
                    request(),
                    model="deepseek-v4.1-flash-1017",
                    provider="bailian",
                    reasoning_effort="medium",
                ),
            )
        assert "product is not activated" in str(caught.value)
        assert not caught.value.retryable
    finally:
        client.close()
