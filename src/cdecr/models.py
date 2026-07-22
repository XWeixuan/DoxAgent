"""Independent DashScope model adapters for the four CDECR model tiers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from time import perf_counter
from typing import Any, Literal

from openai import OpenAI
from pydantic import Field, ValidationError

from cdecr.contracts import StrictModel
from cdecr.ports import (
    EmbeddingResult,
    StructuredModelRequest,
    StructuredModelResult,
)

STRUCTURED_OUTPUT_MODE: Literal["json_object"] = "json_object"
STRUCTURED_REASONING_EFFORT: Literal["none"] = "none"


class ModelTier(StrEnum):
    M1 = "m1"
    M2 = "m2"
    M3 = "m3"
    M4 = "m4"


class ModelAdapterError(RuntimeError):
    """Credential-safe model error suitable for stderr and audit storage."""

    def __init__(
        self,
        *,
        tier: ModelTier,
        code: str,
        status_code: int | None = None,
        latency_ms: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        raw_response_text: str | None = None,
    ) -> None:
        self.tier = tier
        self.code = code
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.raw_response_text = raw_response_text
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"{tier.value} model call failed: {code}{suffix}")


class ProbePayload(StrictModel):
    ok: bool
    tier: ModelTier
    value: int = Field(ge=1, le=1)


def _safe_model_error(exc: Exception, tier: ModelTier, *, started_at: float) -> ModelAdapterError:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    provider_code = body.get("code") if isinstance(body, Mapping) else None
    safe_provider_code: str | None = None
    if isinstance(provider_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", provider_code):
        safe_provider_code = f"provider_{provider_code.lower()}"
    if isinstance(status, int):
        code = safe_provider_code or "provider_http_error"
    elif isinstance(exc, TimeoutError):
        code = "timeout"
    else:
        name = type(exc).__name__.lower()
        code = "timeout" if "timeout" in name else "provider_error"
    return ModelAdapterError(
        tier=tier,
        code=code,
        status_code=status,
        latency_ms=round((perf_counter() - started_at) * 1000),
    )


def _usage_value(usage: object | None, *names: str) -> int | None:
    if usage is None:
        return None
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
        if isinstance(usage, Mapping):
            mapped = usage.get(name)
            if isinstance(mapped, int):
                return mapped
    return None


def _should_rotate_key(exc: Exception) -> bool:
    """Rotate only for key/account/provider failures, never for request timeouts."""

    name = type(exc).__name__.casefold()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return False
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in {400, 401, 403, 429}
    return True


class DashScopeEmbeddingClient:
    """OpenAI-compatible ``text-embedding-v4`` client."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
        timeout_seconds: float = 30.0,
        fallback_api_keys: Sequence[str] = (),
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self._clients: tuple[OpenAI, ...]
        if client is not None:
            self._clients = (client,)
        else:
            keys = [api_key, *(key for key in fallback_api_keys if key and key != api_key)]
            self._clients = tuple(
                OpenAI(
                    api_key=key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                    max_retries=0,
                )
                for key in dict.fromkeys(keys)
            )

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        values = list(texts)
        if not values or len(values) > 10:
            raise ValueError("M1 embedding batches must contain between 1 and 10 texts")
        if any(not value.strip() for value in values):
            raise ValueError("embedding inputs must not be blank")
        started = perf_counter()
        last_error: Exception | None = None
        response: Any | None = None
        for index, client in enumerate(self._clients):
            try:
                response = client.embeddings.create(
                    model=self.model,
                    input=values,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
                break
            except Exception as exc:
                last_error = exc
                if index == len(self._clients) - 1 or not _should_rotate_key(exc):
                    break
        if response is None:
            assert last_error is not None
            raise _safe_model_error(last_error, ModelTier.M1, started_at=started) from last_error
        vectors = [list(item.embedding) for item in response.data]
        if len(vectors) != len(values) or any(len(vector) != self.dimensions for vector in vectors):
            raise ModelAdapterError(
                tier=ModelTier.M1,
                code="invalid_embedding_shape",
                latency_ms=round((perf_counter() - started) * 1000),
            )
        latency_ms = round((perf_counter() - started) * 1000)
        return EmbeddingResult(
            model=self.model,
            dimensions=self.dimensions,
            vectors=vectors,
            input_tokens=_usage_value(getattr(response, "usage", None), "prompt_tokens"),
            latency_ms=latency_ms,
            request_id=getattr(response, "_request_id", None),
        )


class DashScopeStructuredModelClient:
    """M2 Chat Completions and M3/M4 Responses API adapter."""

    def __init__(
        self,
        *,
        tier: ModelTier,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        fallback_api_keys: Sequence[str] = (),
        client: OpenAI | None = None,
    ) -> None:
        if tier is ModelTier.M1:
            raise ValueError("M1 uses DashScopeEmbeddingClient")
        self.tier = tier
        self.model = model
        self._clients: tuple[OpenAI, ...]
        if client is not None:
            self._clients = (client,)
        else:
            keys = [api_key, *(key for key in fallback_api_keys if key and key != api_key)]
            self._clients = tuple(
                OpenAI(
                    api_key=key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                    max_retries=0,
                )
                for key in dict.fromkeys(keys)
            )

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        started = perf_counter()
        schema = json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":"))
        user_prompt = (
            f"{request.user_prompt}\nReturn exactly one valid JSON object matching this JSON "
            f"Schema: {schema}. Do not use Markdown or code fences."
        )
        system_prompt = (
            f"{request.system_prompt}\nReturn exactly one valid JSON object. "
            "Do not use Markdown or code fences."
        )
        last_error: Exception | None = None
        provider_response: Any | None = None
        for index, client in enumerate(self._clients):
            try:
                if self.tier is ModelTier.M2:
                    provider_response = client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {
                                "role": "system",
                                "content": system_prompt,
                            },
                            {"role": "user", "content": user_prompt},
                        ],
                        response_format={"type": STRUCTURED_OUTPUT_MODE},
                        extra_body={"enable_thinking": False},
                    )
                else:
                    response_kwargs: dict[str, Any] = {
                        "model": self.model,
                        "input": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "text": {"format": {"type": STRUCTURED_OUTPUT_MODE}},
                        "reasoning": {"effort": STRUCTURED_REASONING_EFFORT},
                    }
                    provider_response = client.responses.create(**response_kwargs)
                break
            except Exception as exc:
                last_error = exc
                if index == len(self._clients) - 1 or not _should_rotate_key(exc):
                    break
        if provider_response is None:
            assert last_error is not None
            raise _safe_model_error(last_error, self.tier, started_at=started) from last_error

        if self.tier is ModelTier.M2:
            text = provider_response.choices[0].message.content
            usage = getattr(provider_response, "usage", None)
            request_id = getattr(provider_response, "_request_id", None)
            input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
            output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
        else:
            text = provider_response.output_text
            usage = getattr(provider_response, "usage", None)
            request_id = getattr(provider_response, "_request_id", None)
            input_tokens = _usage_value(usage, "input_tokens", "prompt_tokens")
            output_tokens = _usage_value(usage, "output_tokens", "completion_tokens")

        if not isinstance(text, str) or not text.strip():
            raise ModelAdapterError(
                tier=self.tier,
                code="empty_response",
                latency_ms=round((perf_counter() - started) * 1000),
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelAdapterError(
                tier=self.tier,
                code="invalid_json",
                latency_ms=round((perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_response_text=text,
            ) from exc
        if not isinstance(payload, dict):
            raise ModelAdapterError(
                tier=self.tier,
                code="invalid_json_shape",
                latency_ms=round((perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_response_text=text,
            )
        latency_ms = round((perf_counter() - started) * 1000)
        return StructuredModelResult(
            model=self.model,
            payload=payload,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            request_id=request_id,
        )


def probe_models(
    *,
    api_key: str,
    base_url: str,
    tiers: Sequence[ModelTier],
    model_names: Mapping[ModelTier, str],
    dimensions: int = 1024,
    timeout_seconds: float = 30.0,
    fallback_api_keys: Sequence[str] = (),
) -> list[dict[str, object]]:
    """Execute one minimal, schema-validated real probe for each requested tier."""

    results: list[dict[str, object]] = []
    for tier in tiers:
        model = model_names[tier]
        if tier is ModelTier.M1:
            embedding_client = DashScopeEmbeddingClient(
                api_key=api_key,
                base_url=base_url,
                model=model,
                dimensions=dimensions,
                timeout_seconds=timeout_seconds,
                fallback_api_keys=fallback_api_keys,
            )
            embedding_result = embedding_client.embed(["CDECR embedding probe"])
            results.append(
                {
                    "tier": tier.value,
                    "model": embedding_result.model,
                    "ok": True,
                    "dimensions": embedding_result.dimensions,
                    "input_tokens": embedding_result.input_tokens,
                    "latency_ms": embedding_result.latency_ms,
                }
            )
            continue
        structured_client = DashScopeStructuredModelClient(
            tier=tier,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            fallback_api_keys=fallback_api_keys,
        )
        request = StructuredModelRequest(
            system_prompt="You are a deterministic API health probe.",
            user_prompt=f"Return ok=true, tier={tier.value}, and value=1.",
            json_schema=ProbePayload.model_json_schema(),
        )
        structured_result = structured_client.complete(request)
        try:
            payload = ProbePayload.model_validate(structured_result.payload)
        except ValidationError as exc:
            raise ModelAdapterError(
                tier=tier,
                code="schema_validation_failed",
                latency_ms=structured_result.latency_ms,
            ) from exc
        if not payload.ok or payload.tier is not tier or payload.value != 1:
            raise ModelAdapterError(
                tier=tier,
                code="probe_value_mismatch",
                latency_ms=structured_result.latency_ms,
            )
        results.append(
            {
                "tier": tier.value,
                "model": structured_result.model,
                "ok": True,
                "input_tokens": structured_result.input_tokens,
                "output_tokens": structured_result.output_tokens,
                "latency_ms": structured_result.latency_ms,
            }
        )
    return results
