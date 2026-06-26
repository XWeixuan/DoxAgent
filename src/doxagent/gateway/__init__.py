"""Model Gateway contracts and clients."""

from doxagent.gateway.client import ModelClient
from doxagent.gateway.gateway import ModelGateway
from doxagent.gateway.mock import MockModelClient
from doxagent.gateway.providers import (
    AnthropicModelClient,
    BailianChatCompletionsModelClient,
    BailianResponsesModelClient,
    OpenAIModelClient,
)
from doxagent.gateway.schema import (
    GatewayError,
    MessageRole,
    ModelAuditSummary,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderName,
    ResponseFormat,
)
from doxagent.gateway.tracing import (
    TRACE_METADATA_KEYS,
    is_langsmith_wrapped,
    langsmith_extra_from_metadata,
    langsmith_tracing_context,
    mark_langsmith_wrapped,
    run_name_from_metadata,
    tracing_extra_from_metadata,
    wrap_provider_client,
)

__all__ = [
    "AnthropicModelClient",
    "BailianChatCompletionsModelClient",
    "BailianResponsesModelClient",
    "GatewayError",
    "MessageRole",
    "MockModelClient",
    "ModelAuditSummary",
    "ModelClient",
    "ModelGateway",
    "ModelMessage",
    "ModelRequest",
    "ModelResponse",
    "ModelUsage",
    "OpenAIModelClient",
    "ProviderName",
    "ResponseFormat",
    "TRACE_METADATA_KEYS",
    "is_langsmith_wrapped",
    "langsmith_extra_from_metadata",
    "langsmith_tracing_context",
    "mark_langsmith_wrapped",
    "run_name_from_metadata",
    "tracing_extra_from_metadata",
    "wrap_provider_client",
]
