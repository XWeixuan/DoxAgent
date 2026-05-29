"""Model Gateway client protocol and shared helpers."""

from typing import Protocol

from doxagent.gateway.schema import ModelRequest, ModelResponse


class ModelClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one model request."""
