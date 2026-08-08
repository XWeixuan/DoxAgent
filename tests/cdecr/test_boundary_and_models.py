from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import cdecr.models as model_module
from cdecr.config import CDECRSettings
from cdecr.models import (
    DashScopeEmbeddingClient,
    DashScopeStructuredModelClient,
    DeepSeekStructuredModelClient,
    ModelAdapterError,
    ModelTier,
)
from cdecr.ports import StructuredModelRequest, StructuredModelResult


def test_cdecr_never_imports_doxagent() -> None:
    package_root = Path(__file__).resolve().parents[2] / "src" / "cdecr"
    violations: list[str] = []
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            violations.extend(
                name for name in names if name == "doxagent" or name.startswith("doxagent.")
            )
    assert violations == []


class FakeEmbeddings:
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        count = len(kwargs["input"])
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.0] * self.dimensions) for _ in range(count)],
            usage=SimpleNamespace(prompt_tokens=3),
            _request_id="request-1",
        )


class FakeChat:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2),
            _request_id="request-2",
        )


class FakeStrictChat:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="return_cdecr_result", arguments='{"ok":true}'
                                )
                            )
                        ],
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
            _request_id="deepseek-request",
        )


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text='{"ok":true}',
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            _request_id="request-3",
        )


class FakeOpenAI:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings(1024)
        self.chat = SimpleNamespace(completions=FakeChat())
        self.responses = FakeResponses()


def request() -> StructuredModelRequest:
    return StructuredModelRequest(
        system_prompt="system",
        user_prompt="user",
        json_schema={"type": "object"},
    )


def test_structured_request_enforces_json_mode_and_normalizes_model_time_boundary() -> None:
    value = StructuredModelRequest(
        system_prompt="system",
        user_prompt=json.dumps(
            {
                "published_at": "2026-06-25T12:00:00Z",
                "time": {"event_start": "July 22, 2026"},
            }
        ),
        json_schema={
            "type": "object",
            "properties": {
                "event_start": {"type": "string", "format": "date-time"}
            },
        },
    )

    payload = json.loads(value.user_prompt)
    assert value.output_mode == "json_object"
    assert payload["published_at"] == "2026-06-25T08:00:00"
    assert payload["time"]["event_start"] == "2026-07-22"
    assert "format" not in value.json_schema["properties"]["event_start"]  # type: ignore[index]

    result = StructuredModelResult(
        model="fake",
        payload={
            "time": {
                "event_start": "07/22/2026",
                "event_end": "2026-07-22T18:00:00Z",
            }
        },
        latency_ms=1,
    )
    assert result.payload["time"] == {
        "event_start": "2026-07-22",
        "event_end": "2026-07-22T14:00:00",
    }


def test_m1_enforces_batch_and_dimension() -> None:
    fake = FakeOpenAI()
    client = DashScopeEmbeddingClient(
        api_key="key",
        base_url="https://example.test",
        client=fake,
        dimensions=1024,  # type: ignore[arg-type]
    )
    result = client.embed(["one", "two"])
    assert result.dimensions == 1024
    assert len(result.vectors) == 2
    assert fake.embeddings.kwargs["model"] == "qwen3.7-text-embedding"
    assert fake.embeddings.kwargs["dimensions"] == 1024
    with pytest.raises(ValueError, match="between 1 and 10"):
        client.embed([str(index) for index in range(11)])


def test_m2_uses_chat_json_mode_and_disables_thinking() -> None:
    fake = FakeOpenAI()
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key="key",
        base_url="https://example.test",
        model="deepseek-v4-flash",
        client=fake,  # type: ignore[arg-type]
    )
    result = client.complete(request())
    assert result.payload == {"ok": True}
    assert fake.chat.completions.kwargs["response_format"] == {"type": "json_object"}
    assert fake.chat.completions.kwargs["extra_body"] == {"enable_thinking": False}
    assert "JSON" in fake.chat.completions.kwargs["messages"][0]["content"]
    assert "code fences" in fake.chat.completions.kwargs["messages"][0]["content"]


@pytest.mark.parametrize(
    ("tier", "effort"), [(ModelTier.M2, "high"), (ModelTier.M3, "max")]
)
def test_deepseek_uses_thinking_and_strict_function_schema(
    tier: ModelTier, effort: str
) -> None:
    fake = FakeOpenAI()
    strict_chat = FakeStrictChat()
    fake.chat = SimpleNamespace(completions=strict_chat)
    client = DeepSeekStructuredModelClient(
        tier=tier,
        api_key="key",
        base_url="https://api.deepseek.com/beta",
        model="deepseek-v4-flash",
        reasoning_effort=effort,  # type: ignore[arg-type]
        strict=True,
        client=fake,  # type: ignore[arg-type]
    )
    model_request = StructuredModelRequest(
        system_prompt="system",
        user_prompt="user",
        json_schema={
            "title": "Result",
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["ok"],
        },
    )

    result = client.complete(model_request)

    assert result.payload == {"ok": True}
    assert strict_chat.kwargs["reasoning_effort"] == effort
    assert strict_chat.kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    function = strict_chat.kwargs["tools"][0]["function"]
    assert function["strict"] is True
    assert function["parameters"]["required"] == ["ok", "note"]
    assert function["parameters"]["additionalProperties"] is False
    assert "title" not in function["parameters"]
    assert "tool_choice" not in strict_chat.kwargs


def test_deepseek_json_fallback_keeps_thinking_enabled() -> None:
    fake = FakeOpenAI()
    client = DeepSeekStructuredModelClient(
        tier=ModelTier.M2,
        api_key="key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        strict=False,
        client=fake,  # type: ignore[arg-type]
    )

    assert client.complete(request()).payload == {"ok": True}
    kwargs = fake.chat.completions.kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "high"


def test_deepseek_none_disables_thinking_without_sending_effort() -> None:
    fake = FakeOpenAI()
    strict_chat = FakeStrictChat()
    fake.chat = SimpleNamespace(completions=strict_chat)
    client = DeepSeekStructuredModelClient(
        tier=ModelTier.M2,
        api_key="key",
        base_url="https://api.deepseek.com/beta",
        model="deepseek-v4-flash",
        reasoning_effort="none",
        strict=True,
        client=fake,  # type: ignore[arg-type]
    )

    assert client.complete(request()).payload == {"ok": True}
    kwargs = strict_chat.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kwargs


def test_deepseek_strict_inlines_nullable_local_ref_branch() -> None:
    fake = FakeOpenAI()
    strict_chat = FakeStrictChat()
    fake.chat = SimpleNamespace(completions=strict_chat)
    client = DeepSeekStructuredModelClient(
        tier=ModelTier.M2,
        api_key="key",
        base_url="https://api.deepseek.com/beta",
        model="deepseek-v4-flash",
        reasoning_effort="high",
        strict=True,
        client=fake,  # type: ignore[arg-type]
    )
    client.complete(
        StructuredModelRequest(
            system_prompt="system",
            user_prompt="user",
            json_schema={
                "$defs": {"Choice": {"type": "string", "enum": ["A", "B"]}},
                "type": "object",
                "properties": {
                    "choice": {
                        "anyOf": [{"$ref": "#/$defs/Choice"}, {"type": "null"}]
                    }
                },
            },
        )
    )
    choice = strict_chat.kwargs["tools"][0]["function"]["parameters"]["properties"][
        "choice"
    ]
    assert choice["anyOf"][0] == {"type": "string", "enum": ["A", "B"]}
    parameters = strict_chat.kwargs["tools"][0]["function"]["parameters"]
    assert "$defs" not in parameters
    assert '"$ref"' not in json.dumps(parameters)


def test_provider_wire_schema_removes_only_titles() -> None:
    fake = FakeOpenAI()
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key="key",
        base_url="https://example.test",
        model="deepseek-v4-flash",
        client=fake,  # type: ignore[arg-type]
    )
    model_request = StructuredModelRequest(
        system_prompt="system",
        user_prompt="user",
        json_schema={
            "title": "RootTitle",
            "description": "root-description",
            "type": "object",
            "properties": {
                "status": {
                    "title": "StatusTitle",
                    "description": "status-description",
                    "default": "ok",
                    "examples": ["ok"],
                    "type": "string",
                }
            },
        },
    )

    client.complete(model_request)

    wire_prompt = fake.chat.completions.kwargs["messages"][1]["content"]
    assert "RootTitle" not in wire_prompt
    assert "StatusTitle" not in wire_prompt
    assert "root-description" in wire_prompt
    assert "status-description" in wire_prompt
    assert '"default":"ok"' in wire_prompt
    assert '"examples":["ok"]' in wire_prompt
    assert model_request.json_schema["title"] == "RootTitle"


@pytest.mark.parametrize("tier", [ModelTier.M3, ModelTier.M4])
def test_m3_m4_use_responses_json_mode_with_thinking_disabled(tier: ModelTier) -> None:
    fake = FakeOpenAI()
    client = DashScopeStructuredModelClient(
        tier=tier,
        api_key="key",
        base_url="https://example.test",
        model="qwen-test",
        client=fake,  # type: ignore[arg-type]
    )
    result = client.complete(request())
    assert result.payload == {"ok": True}
    assert fake.responses.kwargs["reasoning"] == {"effort": "none"}
    assert fake.responses.kwargs["text"] == {"format": {"type": "json_object"}}
    assert "JSON" in fake.responses.kwargs["input"][0]["content"]
    assert "code fences" in fake.responses.kwargs["input"][0]["content"]


def test_model_errors_are_redacted() -> None:
    class BrokenEmbeddings:
        def create(self, **_: Any) -> Any:
            raise RuntimeError("secret-key and news body")

    fake = FakeOpenAI()
    fake.embeddings = BrokenEmbeddings()  # type: ignore[assignment]
    client = DashScopeEmbeddingClient(
        api_key="secret-key",
        base_url="https://example.test",
        client=fake,  # type: ignore[arg-type]
    )
    with pytest.raises(ModelAdapterError) as caught:
        client.embed(["private news body"])
    assert str(caught.value) == "m1 model call failed: provider_error"


def test_provider_error_code_is_preserved_without_provider_message() -> None:
    class ArrearageError(Exception):
        status_code = 400
        body = {
            "code": "Arrearage",
            "message": "account details and secret material must not escape",
        }

    class BrokenEmbeddings:
        def create(self, **_: Any) -> Any:
            raise ArrearageError

    fake = FakeOpenAI()
    fake.embeddings = BrokenEmbeddings()  # type: ignore[assignment]
    client = DashScopeEmbeddingClient(
        api_key="secret-key",
        base_url="https://example.test",
        client=fake,  # type: ignore[arg-type]
    )
    with pytest.raises(ModelAdapterError) as caught:
        client.embed(["private news body"])
    assert caught.value.code == "provider_arrearage"
    assert "account details" not in str(caught.value)


def test_invalid_json_keeps_private_repair_payload_out_of_error_text() -> None:
    fake = FakeOpenAI()
    fake.responses.output_text = "unused"  # type: ignore[attr-defined]

    class FencedResponses:
        def create(self, **_: Any) -> Any:
            return SimpleNamespace(
                output_text='```json\n{"ok":true}\n```',
                usage=SimpleNamespace(input_tokens=5, output_tokens=8),
                _request_id="request-fenced",
            )

    fake.responses = FencedResponses()  # type: ignore[assignment]
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M3,
        api_key="secret-key",
        base_url="https://example.test",
        model="qwen-test",
        client=fake,  # type: ignore[arg-type]
    )
    with pytest.raises(ModelAdapterError) as caught:
        client.complete(request())
    assert caught.value.code == "invalid_json"
    assert caught.value.input_tokens == 5
    assert caught.value.output_tokens == 8
    assert caught.value.raw_response_text == '```json\n{"ok":true}\n```'
    assert "```" not in str(caught.value)


def test_settings_parse_ordered_deduplicated_fallback_keys() -> None:
    settings = CDECRSettings(
        DASHSCOPE_API_KEY="primary",
        DASHSCOPE_FALLBACK_API_KEY="fallback-one",
        DASHSCOPE_FALLBACK_API_KEYS="primary, fallback-two;fallback-one",
        _env_file=None,
    )  # type: ignore[call-arg]
    assert settings.dashscope_fallback_api_keys() == ("fallback-one", "fallback-two")


def test_settings_keeps_cross_document_dictionary_protocols_mandatory() -> None:
    settings = CDECRSettings(
        CDECR_N9_WIRE_PROTOCOL="on",
        CDECR_N12_WIRE_PROTOCOL="on",
        CDECR_N13_WIRE_PROTOCOL="on",
        CDECR_GROUNDER_ISSUE_PROTOCOL="canary",
        CDECR_TARGETED_REPAIR="legacy",
        _env_file=None,
    )  # type: ignore[call-arg]
    assert settings.n9_wire_protocol == "on"
    assert settings.n12_wire_protocol == "on"
    assert settings.n13_wire_protocol == "on"
    assert settings.grounder_issue_protocol == "canary"
    assert settings.targeted_repair_protocol == "legacy"


def test_settings_parse_deepseek_tier_configuration() -> None:
    settings = CDECRSettings(
        DEEPSEEK_API_KEY="secret",
        CDECR_M2_PROVIDER="deepseek",
        CDECR_M3_PROVIDER="deepseek",
        CDECR_M4_PROVIDER="deepseek",
        CDECR_M2_REASONING_EFFORT="none",
        CDECR_M3_REASONING_EFFORT="low",
        CDECR_M4_REASONING_EFFORT="high",
        CDECR_M2_STRICT="true",
        CDECR_M3_STRICT="true",
        CDECR_M4_STRICT="true",
        _env_file=None,
    )  # type: ignore[call-arg]
    assert settings.require_deepseek() == "secret"
    assert settings.model_m2_provider == "deepseek"
    assert settings.model_m3_provider == "deepseek"
    assert settings.model_m4_provider == "deepseek"
    assert settings.model_m2_reasoning_effort == "none"
    assert settings.model_m3_reasoning_effort == "low"
    assert settings.model_m4_reasoning_effort == "high"
    assert settings.model_m2_strict is True
    assert settings.model_m3_strict is True
    assert settings.model_m4_strict is True


def test_embedding_retries_provider_failure_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted_keys: list[str] = []

    class BrokenEmbeddings:
        def create(self, **_: Any) -> Any:
            raise RuntimeError("primary-secret must stay redacted")

    def factory(*, api_key: str, **_: Any) -> FakeOpenAI:
        attempted_keys.append(api_key)
        fake = FakeOpenAI()
        if api_key == "primary":
            fake.embeddings = BrokenEmbeddings()  # type: ignore[assignment]
        return fake

    monkeypatch.setattr(model_module, "OpenAI", factory)
    client = DashScopeEmbeddingClient(
        api_key="primary",
        fallback_api_keys=("fallback",),
        base_url="https://example.test",
    )
    assert client.embed(["hello"]).dimensions == 1024
    assert attempted_keys == ["primary", "fallback"]


def test_structured_model_retries_provider_failure_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted_keys: list[str] = []

    class BrokenChat:
        def create(self, **_: Any) -> Any:
            raise RuntimeError("primary-secret must stay redacted")

    def factory(*, api_key: str, **_: Any) -> FakeOpenAI:
        attempted_keys.append(api_key)
        fake = FakeOpenAI()
        if api_key == "primary":
            fake.chat = SimpleNamespace(completions=BrokenChat())
        return fake

    monkeypatch.setattr(model_module, "OpenAI", factory)
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key="primary",
        fallback_api_keys=("fallback",),
        base_url="https://example.test",
        model="deepseek-v4-flash",
    )
    assert client.complete(request()).payload == {"ok": True}
    assert attempted_keys == ["primary", "fallback"]


def test_timeout_does_not_multiply_latency_by_rotating_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called_keys: list[str] = []

    class TimedOutEmbeddings:
        def __init__(self, key: str) -> None:
            self.key = key

        def create(self, **_: Any) -> Any:
            called_keys.append(self.key)
            raise TimeoutError("request timed out with secret material")

    def factory(*, api_key: str, **_: Any) -> FakeOpenAI:
        fake = FakeOpenAI()
        fake.embeddings = TimedOutEmbeddings(api_key)  # type: ignore[assignment]
        return fake

    monkeypatch.setattr(model_module, "OpenAI", factory)
    client = DashScopeEmbeddingClient(
        api_key="primary",
        fallback_api_keys=("fallback-one", "fallback-two"),
        base_url="https://example.test",
    )
    with pytest.raises(ModelAdapterError) as caught:
        client.embed(["hello"])
    assert caught.value.code == "timeout"
    assert called_keys == ["primary"]
