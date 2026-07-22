from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import cdecr.models as model_module
from cdecr.config import CDECRSettings
from cdecr.models import (
    DashScopeEmbeddingClient,
    DashScopeStructuredModelClient,
    ModelAdapterError,
    ModelTier,
)
from cdecr.ports import StructuredModelRequest


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
    assert fake.embeddings.kwargs["model"] == "text-embedding-v4"
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
