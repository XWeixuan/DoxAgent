from typing import Any

import pytest

from doxagent.gateway import (
    AnthropicModelClient,
    BailianResponsesModelClient,
    GatewayError,
    MessageRole,
    MockModelClient,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelUsage,
    OpenAIModelClient,
    ProviderName,
    ResponseFormat,
    mark_langsmith_wrapped,
    run_name_from_metadata,
    tracing_extra_from_metadata,
    wrap_provider_client,
)


def request(response_format: ResponseFormat = ResponseFormat.TEXT) -> ModelRequest:
    return ModelRequest(
        provider=ProviderName.MOCK,
        model="mock-model",
        system_prompt="You are a research model.",
        messages=[ModelMessage(role=MessageRole.USER, content="Summarize NVDA narratives.")],
        temperature=0.1,
        max_tokens=256,
        timeout_seconds=5,
        response_format=response_format,
        metadata={
            "ticker": "NVDA",
            "agent_name": "O1",
            "run_id": "run_001",
            "task_type": "generate_expectation_unit",
            "workflow_node": "GenerateExpectationUnits",
        },
    )


@pytest.mark.asyncio
async def test_mock_model_client_returns_offline_response() -> None:
    usage = ModelUsage(input_tokens=3, output_tokens=4, total_tokens=7)
    client = MockModelClient(
        text='{"answer": "ok"}',
        structured={"answer": "ok"},
        raw={"fixture": True},
        usage=usage,
    )

    response = await client.complete(request(ResponseFormat.JSON))

    assert response.succeeded
    assert response.text == '{"answer": "ok"}'
    assert response.structured == {"answer": "ok"}
    assert response.raw == {"fixture": True}
    assert response.usage == usage
    assert response.audit.provider is ProviderName.MOCK
    assert client.calls == 1


@pytest.mark.asyncio
async def test_gateway_primary_success_does_not_trigger_fallback() -> None:
    primary = MockModelClient(text="primary")
    fallback = MockModelClient(text="fallback")
    gateway = ModelGateway(primary, fallbacks=[fallback])

    response = await gateway.complete(request())

    assert response.text == "primary"
    assert response.audit.fallback_used is False
    assert primary.calls == 1
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_gateway_retryable_failure_falls_back() -> None:
    primary = MockModelClient(
        failures=[
            GatewayError(
                code="timeout",
                message="temporary timeout",
                retryable=True,
                provider=ProviderName.MOCK,
            ),
        ],
    )
    fallback = MockModelClient(text="fallback")
    gateway = ModelGateway(primary, fallbacks=[fallback])

    response = await gateway.complete(request())

    assert response.text == "fallback"
    assert response.audit.fallback_used is True
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_gateway_non_retryable_failure_does_not_fallback() -> None:
    primary = MockModelClient(
        failures=[
            GatewayError(
                code="invalid_request",
                message="bad request",
                retryable=False,
                provider=ProviderName.MOCK,
            ),
        ],
    )
    fallback = MockModelClient(text="fallback")
    gateway = ModelGateway(primary, fallbacks=[fallback])

    response = await gateway.complete(request())

    assert response.error is not None
    assert response.error.code == "invalid_request"
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_gateway_parses_json_text_and_rejects_invalid_json() -> None:
    gateway = ModelGateway(MockModelClient(text='{"key": "value"}'))
    response = await gateway.complete(request(ResponseFormat.JSON))
    assert response.structured == {"key": "value"}

    fenced_gateway = ModelGateway(
        MockModelClient(text='Here is the JSON:\n```json\n{"key": "fenced"}\n```')
    )
    fenced_response = await fenced_gateway.complete(request(ResponseFormat.JSON))
    assert fenced_response.error is None
    assert fenced_response.structured == {"key": "fenced"}

    bad_gateway = ModelGateway(MockModelClient(text="not-json"))
    bad_response = await bad_gateway.complete(request(ResponseFormat.JSON))
    assert bad_response.error is not None
    assert bad_response.error.code == "invalid_json"
    assert bad_response.error.retryable is False


class FakeOpenAIResponse:
    output_text = "openai text"

    def model_dump(self) -> dict[str, Any]:
        return {
            "output_text": self.output_text,
            "usage": {"input_tokens": 5, "output_tokens": 6, "total_tokens": 11},
        }


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> FakeOpenAIResponse:
        self.kwargs = kwargs
        return FakeOpenAIResponse()


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


@pytest.mark.asyncio
async def test_openai_adapter_maps_request_to_responses_create() -> None:
    fake_client = FakeOpenAIClient()
    adapter = OpenAIModelClient(fake_client)

    response = await adapter.complete(request(ResponseFormat.JSON))

    assert response.text == "openai text"
    assert response.audit.provider is ProviderName.OPENAI
    assert fake_client.responses.kwargs is not None
    assert fake_client.responses.kwargs["model"] == "mock-model"
    assert fake_client.responses.kwargs["input"][0]["role"] == "system"
    assert fake_client.responses.kwargs["max_output_tokens"] == 256
    assert "langsmith_extra" not in fake_client.responses.kwargs


@pytest.mark.asyncio
async def test_openai_adapter_adds_dynamic_langsmith_run_name_only_when_wrapped() -> None:
    fake_client = FakeOpenAIClient()
    mark_langsmith_wrapped(fake_client)
    adapter = OpenAIModelClient(fake_client)

    await adapter.complete(
        request(ResponseFormat.JSON).model_copy(
            update={"metadata": request().metadata | {"react_step": "2"}},
        )
    )

    assert fake_client.responses.kwargs is not None
    assert fake_client.responses.kwargs["langsmith_extra"] == {
        "name": "O1.GenerateExpectationUnits.LOOP2"
    }


class FakeBailianResponse:
    output_text = ""

    def model_dump(self) -> dict[str, Any]:
        return {
            "output": [
                {"type": "reasoning", "summary": [{"text": "reasoning summary"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "bailian text"}]},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 6, "total_tokens": 11},
        }


class FakeBailianResponses(FakeResponses):
    async def create(self, **kwargs: Any) -> FakeBailianResponse:
        self.kwargs = kwargs
        return FakeBailianResponse()


class FakeBailianClient:
    def __init__(self) -> None:
        self.responses = FakeBailianResponses()


@pytest.mark.asyncio
async def test_bailian_adapter_uses_responses_api_with_thinking_enabled() -> None:
    fake_client = FakeBailianClient()
    adapter = BailianResponsesModelClient(fake_client, enable_thinking=True)

    response = await adapter.complete(request(ResponseFormat.JSON))

    assert response.text == "bailian text"
    assert response.audit.provider is ProviderName.BAILIAN
    assert response.raw["reasoning_summary"] == ["reasoning summary"]
    assert fake_client.responses.kwargs is not None
    assert fake_client.responses.kwargs["model"] == "mock-model"
    assert fake_client.responses.kwargs["extra_body"] == {"enable_thinking": True}


class FakeAnthropicResponse:
    def model_dump(self) -> dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": "anthropic "},
                {"type": "text", "text": "text"},
            ],
            "usage": {"input_tokens": 7, "output_tokens": 8},
        }


class FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> FakeAnthropicResponse:
        self.kwargs = kwargs
        return FakeAnthropicResponse()


class FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = FakeMessages()


@pytest.mark.asyncio
async def test_anthropic_adapter_maps_request_to_messages_create() -> None:
    fake_client = FakeAnthropicClient()
    adapter = AnthropicModelClient(fake_client)

    response = await adapter.complete(request())

    assert response.text == "anthropic text"
    assert response.audit.provider is ProviderName.ANTHROPIC
    assert fake_client.messages.kwargs is not None
    assert fake_client.messages.kwargs["model"] == "mock-model"
    assert fake_client.messages.kwargs["system"] == "You are a research model."
    assert fake_client.messages.kwargs["messages"][0]["role"] == "user"


def test_tracing_wrapper_can_be_disabled() -> None:
    client = object()

    assert (
        wrap_provider_client(ProviderName.OPENAI, client, tracing_enabled=False) is client
    )


def test_bailian_uses_openai_langsmith_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    client = object()
    calls: list[object] = []

    def fake_wrap_openai(value: object, *, tracing_extra: object | None = None) -> object:
        calls.append(tracing_extra)
        return {"wrapped": value}

    monkeypatch.setattr("langsmith.wrappers.wrap_openai", fake_wrap_openai)

    wrapped = wrap_provider_client(
        ProviderName.BAILIAN,
        client,
        tracing_enabled=True,
        tracing_extra={"metadata": {"provider": "bailian"}},
    )

    assert wrapped == {"wrapped": client}
    assert calls == [{"metadata": {"provider": "bailian"}}]


def test_tracing_metadata_uses_expected_keys() -> None:
    extra = tracing_extra_from_metadata(
        request().metadata
        | {
            "react_step": "1",
            "provider": "bailian",
            "model": "qwen3.6-flash",
            "ignored": "value",
        }
    )

    assert extra == {
        "metadata": {
            "ticker": "NVDA",
            "agent_name": "O1",
            "run_id": "run_001",
            "task_type": "generate_expectation_unit",
            "workflow_node": "GenerateExpectationUnits",
            "react_step": "1",
            "provider": "bailian",
            "model": "qwen3.6-flash",
        },
    }


def test_run_name_from_metadata_uses_node_task_fallback_and_loop_default() -> None:
    assert (
        run_name_from_metadata(
            {
                "agent_name": "O1",
                "workflow_node": "GenerateExpectationUnits",
                "task_type": "generate_expectation_unit",
                "react_step": "2",
            }
        )
        == "O1.GenerateExpectationUnits.LOOP2"
    )
    assert (
        run_name_from_metadata(
            {
                "agent_name": "C1",
                "workflow_node": "",
                "task_type": "generate_global_research",
            }
        )
        == "C1.generate_global_research.LOOP1"
    )
