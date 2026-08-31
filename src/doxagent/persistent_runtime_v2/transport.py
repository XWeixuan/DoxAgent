"""Bailian Responses strict-JSON transport for Persistent Runtime V2."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Generic, Protocol, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from cdecr.model_boundary import bailian_strict_wire_schema

T = TypeVar("T", bound=BaseModel)


class RuntimeResponsesError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class RuntimeResponsesRequest(Generic[T]):
    instructions: str
    payload: dict[str, Any]
    output_model: type[T]
    schema_name: str
    previous_response_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeResponsesResult(Generic[T]):
    value: T
    response_id: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cached_input_tokens: int | None


class RuntimeResponsesClient(Protocol):
    model: str

    def complete(self, request: RuntimeResponsesRequest[T]) -> RuntimeResponsesResult[T]: ...


class BailianRuntimeResponsesClient:
    """One-call adapter; orchestration owns retries and durable attempt lineage."""

    provider = "bailian"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "qwen3.8-flash",
        reasoning_effort: str = "medium",
        timeout_seconds: float = 60.0,
        session_cache: bool = True,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("DASHSCOPE_API_KEY is required for Runtime V2")
        if reasoning_effort != "medium":
            raise ValueError("Persistent Runtime V2 reasoning effort is frozen to medium")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.session_cache = session_cache
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def complete(self, request: RuntimeResponsesRequest[T]) -> RuntimeResponsesResult[T]:
        started = perf_counter()
        schema = bailian_strict_wire_schema(request.output_model.model_json_schema())
        schema_text = json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        exact_output_contract = (
            "\n\n# Exact Output Contract\n"
            "Return exactly one JSON object matching the schema below. Use every "
            "top-level key listed in properties, including empty arrays and default "
            "values. Never rename a key, substitute a legacy key, add a key, wrap the "
            "object, or emit Markdown.\n"
            f"{schema_text}"
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": f"{request.instructions}{exact_output_contract}",
            "input": json.dumps(
                request.payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "reasoning": {"effort": self.reasoning_effort},
            "store": True,
            "metadata": request.metadata,
        }
        if request.previous_response_id is not None:
            kwargs["previous_response_id"] = request.previous_response_id
        if self.session_cache:
            kwargs["extra_headers"] = {"x-dashscope-session-cache": "enable"}
        try:
            response = self._client.responses.create(**kwargs)
        except Exception as exc:
            raise _safe_transport_error(exc) from exc

        latency_ms = round((perf_counter() - started) * 1000)
        response_id = str(getattr(response, "id", "") or "")
        text = getattr(response, "output_text", None)
        if not response_id:
            raise RuntimeResponsesError(
                "missing_response_id",
                "Bailian Responses result did not include a response id",
                retryable=True,
            )
        if not isinstance(text, str) or not text.strip():
            raise RuntimeResponsesError(
                "empty_structured_output",
                "Bailian Responses result did not include structured output text",
                retryable=True,
            )
        try:
            value = request.output_model.model_validate_json(text)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeResponsesError(
                "structured_output_validation_failed",
                "Strict structured output validation failed: "
                f"{_validation_error_summary(exc)}",
                retryable=True,
            ) from exc

        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        return RuntimeResponsesResult(
            value=value,
            response_id=response_id,
            latency_ms=latency_ms,
            input_tokens=_usage_int(usage, "input_tokens"),
            output_tokens=_usage_int(usage, "output_tokens"),
            reasoning_tokens=_usage_int(output_details, "reasoning_tokens"),
            cached_input_tokens=_usage_int(details, "cached_tokens"),
        )


def _usage_int(value: Any, field_name: str) -> int | None:
    raw = getattr(value, field_name, None) if value is not None else None
    return int(raw) if isinstance(raw, (int, float)) else None


def _validation_error_summary(exc: Exception) -> str:
    """Return actionable validation details without persisting provider output."""

    if isinstance(exc, ValidationError):
        details = [
            {
                "loc": ".".join(str(part) for part in error.get("loc", ())),
                "type": str(error.get("type", "validation_error")),
                "msg": str(error.get("msg", "validation failed"))[:200],
            }
            for error in exc.errors(include_input=False, include_url=False)[:5]
        ]
        encoded = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
        return f"ValidationError {encoded}"
    if isinstance(exc, json.JSONDecodeError):
        return f"JSONDecodeError line={exc.lineno} column={exc.colno}"
    return type(exc).__name__


def _safe_transport_error(exc: Exception) -> RuntimeResponsesError:
    message = str(exc).lower()
    name = type(exc).__name__.lower()
    if "401" in message or "unauthorized" in message or "authentication" in name:
        return RuntimeResponsesError(
            "provider_auth_failed",
            "Bailian authentication failed",
            retryable=False,
        )
    if "insufficient" in message and ("balance" in message or "quota" in message):
        return RuntimeResponsesError(
            "provider_balance_unavailable",
            "Bailian quota is unavailable",
            retryable=False,
        )
    if "arrearage" in message or "overdue-payment" in message:
        return RuntimeResponsesError(
            "provider_account_in_arrears",
            "Bailian account billing is not in good standing",
            retryable=False,
        )
    if "invalid_json_schema" in message or "json schema" in message and "invalid" in message:
        return RuntimeResponsesError(
            "provider_strict_schema_unsupported",
            "Bailian rejected the strict JSON Schema",
            retryable=False,
        )
    if "429" in message or "rate" in message and "limit" in message:
        return RuntimeResponsesError(
            "provider_rate_limited",
            "Bailian rate limited the request",
            retryable=True,
        )
    if "timeout" in message or "timed out" in message or "timeout" in name:
        return RuntimeResponsesError(
            "provider_timeout",
            "Bailian request timed out",
            retryable=True,
        )
    if any(code in message for code in ("500", "502", "503", "504")):
        return RuntimeResponsesError(
            "provider_server_error",
            "Bailian returned a server error",
            retryable=True,
        )
    return RuntimeResponsesError(
        "provider_request_failed",
        f"Bailian request failed: {type(exc).__name__}",
        retryable=False,
    )
