"""Model Gateway request and response contracts."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models.ids import NonEmptyStr


class GatewayModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")


class ProviderName(StrEnum):
    MOCK = "mock"
    BAILIAN = "bailian"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ResponseFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class ModelMessage(GatewayModel):
    role: MessageRole
    content: NonEmptyStr


class ModelUsage(GatewayModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class GatewayError(GatewayModel):
    code: NonEmptyStr
    message: NonEmptyStr
    retryable: bool
    provider: ProviderName | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ModelAuditSummary(GatewayModel):
    provider: ProviderName
    model: NonEmptyStr
    latency_seconds: float = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    fallback_used: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)
    usage: ModelUsage | None = None


class ModelRequest(GatewayModel):
    provider: ProviderName
    model: NonEmptyStr
    messages: list[ModelMessage] = Field(min_length=1)
    system_prompt: NonEmptyStr | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    response_format: ResponseFormat = ResponseFormat.TEXT
    metadata: dict[str, str] = Field(default_factory=dict)
    json_schema: dict[str, Any] | None = None


class ModelResponse(GatewayModel):
    text: str | None = None
    structured: Any | None = None
    raw: Any | None = None
    usage: ModelUsage | None = None
    audit: ModelAuditSummary
    error: GatewayError | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
