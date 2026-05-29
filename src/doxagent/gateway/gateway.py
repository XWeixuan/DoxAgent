"""Model Gateway orchestration."""

import asyncio
import json
from collections.abc import Sequence

from doxagent.gateway.client import ModelClient
from doxagent.gateway.schema import (
    GatewayError,
    ModelAuditSummary,
    ModelRequest,
    ModelResponse,
    ProviderName,
    ResponseFormat,
)


class ModelGateway:
    def __init__(
        self,
        primary: ModelClient,
        *,
        fallbacks: Sequence[ModelClient] | None = None,
        max_retries: int = 0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.primary = primary
        self.fallbacks = list(fallbacks or [])
        self.max_retries = max_retries

    async def complete(self, request: ModelRequest) -> ModelResponse:
        clients = [self.primary, *self.fallbacks]
        last_response: ModelResponse | None = None
        retry_count = 0

        for client_index, client in enumerate(clients):
            attempts = self.max_retries + 1 if client_index == 0 else 1
            for _ in range(attempts):
                response = await self._complete_once(client, request)
                response.audit.retry_count = retry_count
                response.audit.fallback_used = client_index > 0
                response = self._normalize_structured_response(request, response)
                last_response = response

                if response.error is None:
                    return response
                if not response.error.retryable:
                    return response
                if client_index == 0 and retry_count < self.max_retries:
                    retry_count += 1
                    continue
                break

        if last_response is not None:
            return last_response
        return self._internal_error_response(request, "No model clients were configured.")

    async def _complete_once(self, client: ModelClient, request: ModelRequest) -> ModelResponse:
        if request.timeout_seconds is None:
            return await client.complete(request)
        try:
            return await asyncio.wait_for(client.complete(request), request.timeout_seconds)
        except TimeoutError:
            return self._internal_error_response(
                request,
                "Model request timed out.",
                retryable=True,
            )

    def _normalize_structured_response(
        self,
        request: ModelRequest,
        response: ModelResponse,
    ) -> ModelResponse:
        if response.error is not None or request.response_format is not ResponseFormat.JSON:
            return response
        if response.structured is not None:
            return response
        if response.text is None:
            response.error = GatewayError(
                code="missing_json_text",
                message="JSON response requested, but provider returned no text.",
                retryable=False,
                provider=response.audit.provider,
            )
            return response
        try:
            response.structured = json.loads(response.text)
        except json.JSONDecodeError as exc:
            response.error = GatewayError(
                code="invalid_json",
                message=str(exc),
                retryable=False,
                provider=response.audit.provider,
            )
        return response

    def _internal_error_response(
        self,
        request: ModelRequest,
        message: str,
        *,
        retryable: bool = False,
    ) -> ModelResponse:
        return ModelResponse(
            audit=ModelAuditSummary(
                provider=ProviderName.MOCK,
                model=request.model,
                latency_seconds=0,
                metadata=request.metadata,
            ),
            error=GatewayError(
                code="gateway_error",
                message=message,
                retryable=retryable,
            ),
        )
