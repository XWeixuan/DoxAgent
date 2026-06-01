"""Model Gateway contracts and clients."""

from doxagent.gateway.client import ModelClient
from doxagent.gateway.gateway import ModelGateway
from doxagent.gateway.mock import MockModelClient
from doxagent.gateway.providers import (
    AnthropicModelClient,
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
    langsmith_tracing_context,
    tracing_extra_from_metadata,
    wrap_provider_client,
)

__all__ = [
    "AnthropicModelClient",
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
    "langsmith_tracing_context",
    "tracing_extra_from_metadata",
    "wrap_provider_client",
]
