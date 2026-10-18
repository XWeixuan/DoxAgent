import json
from dataclasses import replace
from typing import Any, cast

import httpx
import pytest

from doxagent.persistent_runtime_v2.openrouter import (
    HAIKU_ENDPOINT,
    HAIKU_MODEL,
    W2R1OpenRouterClient,
    build_w2_r1_cached_request,
)
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import RuntimeModelTurn, W2Round1RecallResult
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import (
    BailianRuntimeResponsesClient,
    RuntimeResponsesError,
    RuntimeResponsesRequest,
)
from doxagent.settings import DoxAgentSettings
from tests.test_persistent_runtime_v2 import (
    _FakeKnownEvents,
    _FakePolicies,
    _FakeResponses,
    _source,
)


def request():
    return RuntimeResponsesRequest(
        instructions="Recall Policy scopes",
        payload={
            "source_message": {"title": "new message"},
            "runtime_policy_projection": {
                "policies": [{"policy_id": "P1", "match_scope": "facts"}]
            },
        },
        output_model=W2Round1RecallResult,
        schema_name="w2_round1_recall_result",
        cache_context_keys=("runtime_policy_projection",),
        model=HAIKU_MODEL,
        reasoning_effort="high",
        timeout_seconds=180,
        provider="openrouter",
        provider_endpoint=HAIKU_ENDPOINT,
        cache_ttl="1h",
        metadata={"lane": "W2", "round": "R1"},
    )


def bailian():
    fake = _FakeResponses()
    fake.reasoning_effort = "medium"
    fake.timeout_seconds = 120
    fake.w2_r1_timeout_seconds = 180
    # Real historical defaults, with offline completion for non-OpenRouter rounds.
    config = BailianRuntimeResponsesClient(api_key="offline", base_url="https://unused.invalid")
    fake.model_settings = config.model_settings
    return fake


def stream_response(*, output=None, finish="stop", usage=True):
    if output is None:
        output = {"candidate_policy_ids": [], "reason": "no match"}
    chunks = [
        dict(
            id="gen-test",
            provider="Google",
            choices=[dict(delta={"content": json.dumps(output)}, finish_reason=finish)],
        )
    ]
    if usage:
        chunks.append(
            dict(
                choices=[],
                usage=dict(
                    prompt_tokens=1000,
                    completion_tokens=30,
                    cost=0.0001,
                    prompt_tokens_details=dict(cached_tokens=900, cache_write_tokens=50),
                    completion_tokens_details=dict(reasoning_tokens=10),
                ),
            )
        )
    return httpx.Response(
        200, text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    )


def test_default_settings_and_tested_request_shape():
    assert DoxAgentSettings(_env_file=None).persistent_runtime_v2_w2_r1_model == HAIKU_MODEL
    a, fingerprint = build_w2_r1_cached_request(request())
    b, other = build_w2_r1_cached_request(
        replace(request(), payload={**request().payload, "source_message": {"title": "other"}})
    )
    assert a["messages"][0] == b["messages"][0]
    assert a["messages"][1]["content"][0] == b["messages"][1]["content"][0]
    assert fingerprint == other and a["session_id"] == b["session_id"]
    assert a["provider"] == dict(
        only=[HAIKU_ENDPOINT], allow_fallbacks=False, require_parameters=True
    )
    assert a["reasoning"] == {"effort": "high"}
    assert a["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"
    assert a["messages"][1]["content"][0]["cache_control"]["ttl"] == "1h"
    assert "cache_control" not in a["messages"][1]["content"][1]
    assert "previous_response_id" not in a
    assert "metadata" not in a  # per-Case identity never enters the cached prefix


def test_stream_usage_and_history_round_routing():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return stream_response()

    original = bailian()
    client = W2R1OpenRouterClient(
        cast(Any, original),
        api_key="offline",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.complete(request())
    assert result.cached_input_tokens == 900 and result.cache_write_input_tokens == 50
    assert result.reasoning_tokens == 10 and result.provider_usage["cost"] == 0.0001
    assert result.provider_usage["response_provider"] == "Google"
    # Frozen DeepSeek and Qwen requests must not silently migrate to Haiku.
    for model in ("deepseek-v4.1-flash", "qwen3.8-flash"):
        client.complete(replace(request(), model=model, provider="bailian", reasoning_effort="low"))
    assert len(calls) == 1 and len(original.calls) == 2
    with pytest.raises(RuntimeResponsesError, match="W2R1 only"):
        client.complete(replace(request(), metadata={"lane": "W2", "round": "R2"}))
    client.close()


@pytest.mark.parametrize(
    "status,retryable,code",
    [
        (402, False, "provider_balance_unavailable"),
        (401, False, "provider_auth_failed"),
        (429, True, "provider_rate_limited"),
        (503, True, "provider_request_failed"),
    ],
)
def test_http_errors_preserve_retry_policy(status, retryable, code):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as http:
        client = W2R1OpenRouterClient(cast(Any, bailian()), api_key="offline", client=http)
        with pytest.raises(RuntimeResponsesError) as caught:
            client.complete(request())
        assert caught.value.code == code and caught.value.retryable == retryable


@pytest.mark.parametrize(
    "output,finish,code",
    [
        (
            {"candidate_policy_ids": [], "reason": "no match", "extra": True},
            "stop",
            "structured_output_validation_failed",
        ),
        (
            {"candidate_policy_ids": [], "reason": "no match"},
            "length",
            "incomplete_structured_output",
        ),
    ],
)
def test_invalid_or_truncated_outputs_retain_cache_usage(output, finish, code):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: stream_response(output=output, finish=finish))
    ) as http:
        client = W2R1OpenRouterClient(cast(Any, bailian()), api_key="offline", client=http)
        with pytest.raises(RuntimeResponsesError) as caught:
            client.complete(request())
        assert caught.value.code == code and caught.value.retryable
        assert caught.value.usage["cache_write_input_tokens"] == 50


def test_timeout_is_retryable_and_does_not_fallback():
    def handler(req):
        raise httpx.ReadTimeout("offline timeout", request=req)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        original = bailian()
        client = W2R1OpenRouterClient(cast(Any, original), api_key="offline", client=http)
        with pytest.raises(RuntimeResponsesError) as caught:
            client.complete(request())
        assert caught.value.code == "provider_timeout" and caught.value.retryable
        assert not original.calls


@pytest.mark.parametrize("invalid_first", [False, True])
def test_service_records_and_freezes_provider_settings_and_cache_receipts(tmp_path, invalid_first):
    repository = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime.sqlite3")
    calls = []

    def handler(req):
        calls.append(req)
        if invalid_first and len(calls) == 1:
            return stream_response(
                output={"candidate_policy_ids": ["outside-projection"], "reason": "bad"}
            )
        return stream_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        original = bailian()
        client = W2R1OpenRouterClient(cast(Any, original), api_key="offline", client=http)
        service = PersistentRuntimeV2Service(
            repository=repository,
            responses=client,
            known_events=_FakeKnownEvents(),
            policies=_FakePolicies(),
            dispatch_effects=False,
            retry_delays_seconds=(0,),
            sleep=lambda _: None,
        )
        try:
            case = service.execute_message(_source())
            turns = repository.list_turns(case.case_id)
            r1_turns = [t for t in turns if (t.lane, t.round_name) == ("W2", "R1")]
            turn = r1_turns[-1]
            if invalid_first:
                assert len(r1_turns) == 2
                assert r1_turns[0].error_code == "semantic_output_validation_failed"
                assert r1_turns[0].provider_usage["cost"] == 0.0001
            assert turn.provider == "openrouter" and turn.model == HAIKU_MODEL
            assert turn.provider_endpoint == HAIKU_ENDPOINT and turn.cache_ttl == "1h"
            assert turn.transport == "chat_completions_json_schema"
            assert turn.reasoning_effort == "high" and turn.timeout_seconds == 180
            assert turn.cached_input_tokens == 900 and turn.cache_write_input_tokens == 50
            assert turn.provider_usage["cost"] == 0.0001
            assert all(t.provider == "bailian" for t in turns if t not in r1_turns)
            old_config = service._round_model_settings(case, "W2", "R1")
            client.w2_r1_model = "later-default"
            assert service._round_model_settings(case, "W2", "R1") == old_config
            # Persisted historical data still reads with original provider defaults.
            legacy = turn.model_dump(
                exclude={
                    "provider",
                    "provider_endpoint",
                    "cache_ttl",
                    "provider_usage",
                    "cache_write_input_tokens",
                }
            )
            legacy.update(
                model="deepseek-v4.1-flash",
                reasoning_effort="low",
                transport="responses_json_object",
            )
            assert RuntimeModelTurn.model_validate(legacy).provider == "bailian"
        finally:
            service.close()
