"""Offline mock model client for tests and future workflow fixtures."""

from collections import deque
from time import perf_counter
from typing import Any

from doxagent.gateway.schema import (
    GatewayError,
    ModelAuditSummary,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderName,
)


class MockModelClient:
    def __init__(
        self,
        *,
        text: str = "mock response",
        structured: Any | None = None,
        raw: Any | None = None,
        usage: ModelUsage | None = None,
        failures: list[GatewayError] | None = None,
    ) -> None:
        self.text = text
        self.structured = structured
        self.raw = raw if raw is not None else {"provider": ProviderName.MOCK}
        self.usage = usage
        self.failures = deque(failures or [])
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        started_at = perf_counter()
        audit = ModelAuditSummary(
            provider=ProviderName.MOCK,
            model=request.model,
            latency_seconds=perf_counter() - started_at,
            metadata=request.metadata,
            usage=self.usage,
        )

        if self.failures:
            error = self.failures.popleft()
            return ModelResponse(raw=self.raw, usage=self.usage, audit=audit, error=error)

        return ModelResponse(
            text=self.text,
            structured=self.structured,
            raw=self.raw,
            usage=self.usage,
            audit=audit,
        )
