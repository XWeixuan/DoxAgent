import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.openrouter import (
    RuntimeRecallOpenRouterClient,
    build_w2_r1_cached_request,
)
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
from doxagent.persistent_runtime_v2.vertex import VertexClaudeClient, vertex_body
from tests.test_cost_future_transports import native_request
from tests.test_w2_r1_openrouter import bailian


def test_native_migration_preserves_every_cache_block_and_model_parameter():
    req = native_request()
    old, fingerprint = build_w2_r1_cached_request(req)
    native, other = vertex_body(req)
    assert native["system"] == old["messages"][0]["content"]
    assert native["messages"] == old["messages"][1:]
    assert native["max_tokens"] == old["max_tokens"] == 16384
    assert native["output_config"]["effort"] == old["reasoning"]["effort"] == "high"
    assert (
        native["output_config"]["format"]["schema"]
        == old["response_format"]["json_schema"]["schema"]
    )
    assert fingerprint == other
    assert native["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert native["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"


@pytest.mark.parametrize("array_envelope", [False, True])
def test_api_key_rejection_is_actionable_redacted_and_has_no_fallback(array_envelope):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["x-goog-api-key"] == "test-google-key"
        assert "authorization" not in request.headers
        assert "test-google-key" not in str(request.url)
        error = {"error": {"message": "API keys are not supported: test-google-key"}}
        return httpx.Response(401, json=[error] if array_envelope else error)

    client = VertexClaudeClient(
        "p",
        api_key="test-google-key",
        token_provider=lambda: pytest.fail("Must not silently switch credentials"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(RuntimeResponsesError) as caught:
        client.complete(native_request())
    assert caught.value.code == "provider_auth_failed" and not caught.value.retryable
    assert "API keys are not supported" in str(caught.value)
    assert "test-google-key" not in str(caught.value)
    assert len(seen) == 1
    assert caught.value.usage["input_tokens"] is None


def test_vertex_profile_preserves_current_timeout_and_w1_chunk_size():
    config = configured_rounds(
        SimpleNamespace(
            persistent_runtime_v2_api_profile="vertex_recall",
            vertex_project="my-project",
            persistent_runtime_v2_timeout_seconds=121,
            persistent_runtime_v2_w2_r1_timeout_seconds=181,
            persistent_runtime_v2_w1_provisional_cache_batch_size=7,
        )
    )
    assert config["W1:R1"]["timeout_seconds"] == 121
    assert config["W2:R1"]["timeout_seconds"] == 181
    assert config["W1:R1"]["provisional_cache_batch_size"] == 7
    assert {c["provider_endpoint"] for c in config.values()} == {"global"}
    client = RuntimeRecallOpenRouterClient(
        bailian(),
        api_key="",
        round_overrides=config,
        vertex_project="my-project",
    )
    try:
        frozen = client.model_settings()
        assert frozen["rounds"]["W2:R1"]["provider"] == "vertex"
        assert "test-google-key" not in json.dumps(frozen)
        assert frozen["vertex_project"] == "my-project"
    finally:
        client.close()


def test_frozen_openrouter_case_still_uses_its_original_route():
    # The production dispatcher selects provider from the frozen request, not the new profile.
    config = configured_rounds(
        SimpleNamespace(persistent_runtime_v2_api_profile="vertex_recall", vertex_project="p")
    )
    calls = []

    def handler(request):
        calls.append(str(request.url))
        from tests.test_w2_r1_openrouter import stream_response

        return stream_response()

    client = RuntimeRecallOpenRouterClient(
        bailian(),
        api_key="old-key",
        round_overrides=config,
        vertex_project="p",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        client.complete(
            replace(
                native_request(),
                provider="openrouter",
                model="anthropic/claude-haiku-5.5",
                provider_endpoint="google-vertex/global",
            )
        )
        assert calls == ["https://openrouter.ai/api/v1/chat/completions"]
        assert client._vertex is None
    finally:
        client.close()
