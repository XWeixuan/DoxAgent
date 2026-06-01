"""MAF chat client backed by DoxAgent Model Gateway."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, Message

from doxagent.gateway import (
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ProviderName,
    ResponseFormat,
)

MetadataBuilder = Callable[[Mapping[str, Any]], dict[str, str]]


class ModelGatewayChatClient(BaseChatClient[dict[str, Any]]):
    """Adapt MAF chat calls to DoxAgent's provider-neutral ModelGateway."""

    OTEL_PROVIDER_NAME = "doxagent-model-gateway"

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        provider: ProviderName = ProviderName.MOCK,
        model: str = "mock-model",
        response_format: ResponseFormat = ResponseFormat.JSON,
        metadata_builder: MetadataBuilder | None = None,
    ) -> None:
        super().__init__()
        self.gateway = gateway
        self.provider = provider
        self.model = model
        self.response_format = response_format
        self.metadata_builder = metadata_builder
        self.last_model_response: ModelResponse | None = None
        self.last_model_request: ModelRequest | None = None

    async def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> ChatResponse:
        if stream:
            raise NotImplementedError("DoxAgent MAF runtime does not support streaming yet.")

        request = self._to_model_request(messages, options, kwargs)
        self.last_model_request = request
        response = await self.gateway.complete(request)
        self.last_model_response = response
        return ChatResponse(
            messages=[Message(role="assistant", contents=[self._response_text(response)])],
            model=response.audit.model,
            usage_details=None,
            raw_representation=response.raw,
        )

    def _to_model_request(
        self,
        messages: Sequence[Message],
        options: Mapping[str, Any],
        kwargs: Mapping[str, Any],
    ) -> ModelRequest:
        metadata_input = {
            **{str(key): value for key, value in options.items()},
            **{str(key): value for key, value in kwargs.items()},
        }
        metadata = (
            self.metadata_builder(metadata_input)
            if self.metadata_builder is not None
            else _string_metadata(metadata_input)
        )
        return ModelRequest(
            provider=self.provider,
            model=str(options.get("model") or self.model),
            messages=[
                ModelMessage(
                    role=_message_role(message),
                    content=_message_content(message),
                )
                for message in messages
            ],
            temperature=_optional_float(options.get("temperature")),
            max_tokens=_optional_int(options.get("max_tokens")),
            timeout_seconds=_optional_float(options.get("timeout_seconds")),
            response_format=self.response_format,
            metadata=metadata,
        )

    def _response_text(self, response: ModelResponse) -> str:
        if response.error is not None:
            return json.dumps(
                {
                    "status": "failed",
                    "error": response.error.model_dump(mode="json"),
                },
                ensure_ascii=True,
            )
        if response.text is not None:
            return response.text
        if response.structured is not None:
            return json.dumps(response.structured, ensure_ascii=True)
        return ""


def _message_role(message: Message) -> MessageRole:
    role = str(message.role)
    if role == "assistant":
        return MessageRole.ASSISTANT
    if role == "system":
        return MessageRole.SYSTEM
    return MessageRole.USER


def _message_content(message: Message) -> str:
    parts: list[str] = []
    for content in message.contents or []:
        parts.append(str(content))
    joined = "\n".join(part for part in parts if part.strip())
    return joined or "empty message"


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _string_metadata(values: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, str):
            result[str(key)] = value
        else:
            result[str(key)] = json.dumps(value, ensure_ascii=True, default=str)
    return result
