"""Independent provider adapters for the four CDECR model tiers."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from time import perf_counter
from typing import Any, Literal

from openai import AsyncOpenAI, OpenAI
from pydantic import Field, ValidationError

from cdecr.contracts import StrictModel
from cdecr.model_boundary import bailian_strict_wire_schema, compact_wire_schema
from cdecr.ports import (
    EmbeddingResult,
    ResponsesModelRequest,
    StructuredModelRequest,
    StructuredModelResult,
)
from cdecr.provider_resilience import (
    DEFAULT_KEY_HEALTH,
    ProviderKeyHealthRegistry,
    classify_provider_error,
    key_fingerprint,
)

STRUCTURED_OUTPUT_MODE: Literal["json_object"] = "json_object"
STRUCTURED_REASONING_EFFORT: Literal["none"] = "none"
DEEPSEEK_TOOL_NAME = "return_cdecr_result"


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
        provider_key_fingerprint: str | None = None,
        parse_diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        self.tier = tier
        self.code = code
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.raw_response_text = raw_response_text
        self.provider_key_fingerprint = provider_key_fingerprint
        self.parse_diagnostics = dict(parse_diagnostics or {})
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"{tier.value} model call failed: {code}{suffix}")


class ProbePayload(StrictModel):
    ok: bool
    tier: ModelTier
    value: int = Field(ge=1, le=1)


def _safe_model_error(
    exc: Exception,
    tier: ModelTier,
    *,
    started_at: float,
    provider_key: str | None = None,
) -> ModelAdapterError:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    provider_code = body.get("code") if isinstance(body, Mapping) else None
    safe_provider_code: str | None = None
    if isinstance(provider_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", provider_code):
        safe_provider_code = f"provider_{provider_code.lower()}"
    if status == 402:
        code = "provider_arrearage"
    elif isinstance(status, int):
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
        provider_key_fingerprint=_selected_key_fingerprint(provider_key),
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


def _reasoning_usage_value(usage: object | None) -> int | None:
    if usage is None:
        return None
    details = getattr(usage, "completion_tokens_details", None)
    if details is None and isinstance(usage, Mapping):
        details = usage.get("completion_tokens_details")
    if details is None:
        details = getattr(usage, "output_tokens_details", None)
    if details is None and isinstance(usage, Mapping):
        details = usage.get("output_tokens_details")
    return _usage_value(details, "reasoning_tokens", "thinking_tokens")


def _cached_input_usage_value(usage: object | None) -> int | None:
    if usage is None:
        return None
    details = getattr(usage, "input_tokens_details", None)
    if details is None and isinstance(usage, Mapping):
        details = usage.get("input_tokens_details")
    return _usage_value(details, "cached_tokens", "cached_input_tokens")


def _selected_key_fingerprint(key: str | None) -> str | None:
    return key_fingerprint(key) if isinstance(key, str) and key != "injected" else None


def _should_rotate_key(exc: Exception) -> bool:
    """Rotate only for key/account/provider failures, never for request timeouts."""

    name = type(exc).__name__.casefold()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return False
    status = getattr(exc, "status_code", None)
    code = str(getattr(exc, "code", "")).casefold()
    body = getattr(exc, "body", None)
    provider_code = str(body.get("code", "")).casefold() if isinstance(body, Mapping) else ""
    if provider_code in {"arrearage", "insufficient_balance"}:
        return True
    if provider_code in {"invalidparameter", "invalid_request", "invalid_request_error"}:
        return False
    if "invalid_request" in code or "invalid_parameter" in code:
        return False
    if isinstance(status, int):
        return status in {401, 403, 429} or (status == 400 and not provider_code)
    return True


def _structured_result_from_text(
    *,
    tier: ModelTier,
    model: str,
    text: object,
    input_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    request_id: str | None,
    started_at: float,
    cached_input_tokens: int | None = None,
    response_id: str | None = None,
    payload_override: object | None = None,
    transport: Literal[
        "chat_json_object",
        "chat_json_schema",
        "responses_json_object",
        "responses_json_schema",
    ]
    | None = None,
    output_mode: Literal["json_object", "json_schema"] | None = None,
    effective_reasoning_effort: Literal["none", "low", "high", "max"] | None = None,
    provider_key_fingerprint: str | None = None,
) -> StructuredModelResult:
    if not isinstance(text, str) or not text.strip():
        raise ModelAdapterError(
            tier=tier,
            code="empty_response",
            latency_ms=round((perf_counter() - started_at) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    diagnostics: dict[str, object] = {}
    if payload_override is not None:
        payload = payload_override
        diagnostics = {"normalization": "request_specific"}
    else:
        try:
            payload, diagnostics = _parse_single_json_payload(text)
        except json.JSONDecodeError as exc:
            raise ModelAdapterError(
                tier=tier,
                code="invalid_json",
                latency_ms=round((perf_counter() - started_at) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_response_text=text,
                provider_key_fingerprint=provider_key_fingerprint,
                parse_diagnostics=_json_failure_diagnostics(text, exc),
            ) from exc
    if not isinstance(payload, dict):
        raise ModelAdapterError(
            tier=tier,
            code="invalid_json_shape",
            latency_ms=round((perf_counter() - started_at) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response_text=text,
            provider_key_fingerprint=provider_key_fingerprint,
            parse_diagnostics=diagnostics,
        )
    return StructuredModelResult(
        model=model,
        payload=payload,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
        latency_ms=round((perf_counter() - started_at) * 1000),
        request_id=request_id,
        response_id=response_id,
        transport=transport,
        output_mode=output_mode,
        effective_reasoning_effort=effective_reasoning_effort,
        provider_key_fingerprint=provider_key_fingerprint,
        parse_diagnostics=diagnostics,
    )


def _json_failure_diagnostics(text: str, exc: json.JSONDecodeError) -> dict[str, object]:
    prefix = text[:128]
    suffix = text[-128:]
    return {
        "response_chars": len(text),
        "parse_offset": exc.pos,
        "parse_error": exc.msg[:120],
        "starts_with_fence": text.lstrip().startswith("```"),
        "ends_with_fence": text.rstrip().endswith("```"),
        "prefix_sha256": __import__("hashlib").sha256(prefix.encode("utf-8")).hexdigest()[:16],
        "suffix_sha256": __import__("hashlib").sha256(suffix.encode("utf-8")).hexdigest()[:16],
    }


def _parse_single_json_payload(text: str) -> tuple[object, dict[str, object]]:
    """Parse one unambiguous JSON value with two bounded wire normalizations."""

    stripped = text.strip()
    try:
        return json.loads(stripped), {"normalization": "direct", "response_chars": len(text)}
    except json.JSONDecodeError as direct_error:
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            stripped,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced is not None:
            return json.loads(fenced.group(1)), {
                "normalization": "single_code_fence",
                "response_chars": len(text),
            }

        decoder = json.JSONDecoder()
        decoded: list[tuple[object, int, int]] = []
        for start, character in enumerate(stripped):
            if character != "{":
                continue
            try:
                value, length = decoder.raw_decode(stripped[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                decoded.append((value, start, start + length))
        unique = {
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")): (
                value,
                start,
                end,
            )
            for value, start, end in decoded
        }
        if len(unique) == 1:
            value, start, end = next(iter(unique.values()))
            return value, {
                "normalization": "single_embedded_object",
                "response_chars": len(text),
                "prefix_chars": start,
                "suffix_chars": len(stripped) - end,
            }
        raise direct_error


def _responses_input_with_schema(request: ResponsesModelRequest) -> list[dict[str, Any]]:
    values = copy.deepcopy(request.input)
    if request.output_mode == "json_schema":
        return values
    schema = json.dumps(
        compact_wire_schema(request.json_schema),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    instruction = (
        "Return exactly one valid JSON object matching this JSON Schema: "
        f"{schema}. Do not use Markdown or code fences."
    )
    for item in reversed(values):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = f"{content}\n{instruction}"
            return values
    values.append({"role": "user", "content": instruction})
    return values


def _responses_kwargs(
    *,
    model: str,
    request: ResponsesModelRequest,
    session_cache_header: bool,
) -> dict[str, Any]:
    format_payload: dict[str, Any]
    if request.output_mode == "json_schema":
        format_payload = {
            "type": "json_schema",
            "name": request.schema_name,
            "schema": compact_wire_schema(request.json_schema),
            "strict": request.strict,
        }
    else:
        format_payload = {"type": request.output_mode}
    kwargs: dict[str, Any] = {
        "model": model,
        "input": _responses_input_with_schema(request),
        "text": {"format": format_payload},
        "reasoning": {"effort": request.reasoning_effort},
    }
    if request.previous_response_id is not None:
        kwargs["previous_response_id"] = request.previous_response_id
    if request.session_cache and session_cache_header:
        kwargs["extra_headers"] = {"x-dashscope-session-cache": "enable"}
    return kwargs


def _chat_json_schema_kwargs(
    *,
    model: str,
    request: ResponsesModelRequest,
) -> dict[str, Any]:
    """Compile Bailian's documented Chat Completions JSON Schema mode."""

    if request.output_mode != "json_schema":
        raise ValueError("Chat JSON Schema transport requires output_mode=json_schema")
    return {
        "model": model,
        "messages": copy.deepcopy(request.input),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": request.schema_name,
                "strict": request.strict,
                "schema": compact_wire_schema(request.json_schema),
            },
        },
        "reasoning_effort": request.reasoning_effort,
    }


def _structured_chat_kwargs(
    *,
    model: str,
    request: StructuredModelRequest,
    strict: bool,
    reasoning_effort: Literal["none", "low", "high", "max"],
) -> dict[str, Any]:
    """Build Bailian Chat Completions kwargs without putting the schema in the prompt."""

    if strict or request.output_mode == "json_schema":
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": bailian_strict_wire_schema(request.json_schema),
                },
            },
        }
        if reasoning_effort != "none":
            kwargs["reasoning_effort"] = reasoning_effort
        else:
            kwargs["extra_body"] = {"enable_thinking": False}
        return kwargs
    schema = json.dumps(
        compact_wire_schema(request.json_schema), ensure_ascii=False, separators=(",", ":")
    )
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{request.system_prompt}\nReturn exactly one valid JSON object. "
                    "Do not use Markdown or code fences."
                ),
            },
            {
                "role": "user",
                "content": f"{request.user_prompt}\nReturn JSON matching this schema: {schema}",
            },
        ],
        "response_format": {"type": "json_object"},
        "extra_body": {"enable_thinking": False},
    }


def _responses_json_schema_kwargs(
    *,
    model: str,
    request: ResponsesModelRequest,
) -> dict[str, Any]:
    return {
        "model": model,
        "input": _responses_input_with_schema(request),
        "text": {
            "format": {
                "type": "json_schema",
                "name": request.schema_name,
                "strict": True,
                "schema": bailian_strict_wire_schema(request.json_schema),
            }
        },
        "reasoning": {"effort": request.reasoning_effort},
        **(
            {"previous_response_id": request.previous_response_id}
            if request.previous_response_id is not None
            else {}
        ),
        **(
            {"extra_headers": {"x-dashscope-session-cache": "enable"}}
            if request.session_cache
            else {}
        ),
    }


def _deepseek_thinking_kwargs(
    effort: Literal["none", "low", "high", "max"],
) -> dict[str, Any]:
    if effort == "none":
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {
        "reasoning_effort": effort,
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def _response_payload_for_request(text: object, request: ResponsesModelRequest) -> object | None:
    """Tolerate provider wire-shape drift for one known local-invalid contract.

    Bailian Responses has occasionally emitted Dreamer evidence arrays instead of the
    declared wrapper object.  Preserve those legal evidence items as independent
    candidates; all other contracts remain strict and are rejected normally.
    """

    if not isinstance(text, str):
        return None
    try:
        payload, _ = _parse_single_json_payload(text)
    except json.JSONDecodeError:
        return None
    if request.schema_name != "cdecr_dreamer_output" or not isinstance(payload, list):
        return None
    for candidate_payload in [payload]:
        normalized: list[dict[str, object]] = []
        for item in candidate_payload:
            if not isinstance(item, Mapping):
                continue
            if isinstance(item.get("statement"), str) and isinstance(
                item.get("evidence_locations"), list
            ):
                normalized.append(dict(item))
                continue
            segment_id = item.get("segment_id")
            evidence_text = item.get("text")
            if isinstance(segment_id, str) and isinstance(evidence_text, str) and evidence_text:
                normalized.append(
                    {
                        "statement": evidence_text,
                        "evidence_locations": [{"segment_id": segment_id, "text": evidence_text}],
                    }
                )
        return {"candidates": normalized}
    return None


class DashScopeEmbeddingClient:
    """OpenAI-compatible DashScope text embedding client."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "qwen3.7-text-embedding",
        dimensions: int = 1024,
        timeout_seconds: float = 30.0,
        fallback_api_keys: Sequence[str] = (),
        key_health: ProviderKeyHealthRegistry | None = None,
        key_rotation_enabled: bool = False,
        auto_quarantine_enabled: bool = False,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self._key_health = key_health or DEFAULT_KEY_HEALTH
        self._key_rotation_enabled = key_rotation_enabled
        self._auto_quarantine_enabled = auto_quarantine_enabled
        self._api_keys: tuple[str, ...]
        self._clients: tuple[OpenAI, ...]
        if client is not None:
            self._api_keys = ("injected",)
            self._clients = (client,)
        else:
            keys = [api_key, *(key for key in fallback_api_keys if key and key != api_key)]
            self._api_keys = tuple(dict.fromkeys(keys))
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
        last_attempted_key: str | None = None
        response: Any | None = None
        pairs = tuple(zip(self._api_keys, self._clients, strict=True))
        if not self._key_rotation_enabled:
            pairs = pairs[:1]
        for index, (key, client) in enumerate(pairs):
            if (
                self._auto_quarantine_enabled
                and key != "injected"
                and not self._key_health.healthy(key)
            ):
                continue
            try:
                last_attempted_key = key
                from cdecr.usage_capture import call

                response = call(
                    client.embeddings.create,
                    _usage_provider="bailian",
                    _usage_node="EMBEDDING",
                    model=self.model,
                    input=values,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
                if self._auto_quarantine_enabled:
                    self._key_health.record_success(key)
                break
            except Exception as exc:
                last_error = exc
                if self._auto_quarantine_enabled:
                    self._key_health.record_failure(key, classify_provider_error(exc))
                if index == len(pairs) - 1 or not _should_rotate_key(exc):
                    break
        if response is None:
            if last_error is None:
                raise ModelAdapterError(
                    tier=ModelTier.M1,
                    code="provider_all_keys_unavailable",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            raise _safe_model_error(
                last_error,
                ModelTier.M1,
                started_at=started,
                provider_key=last_attempted_key,
            ) from last_error
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
    """DashScope Responses JSON Object adapter with opt-in JSON Schema strict."""

    def __init__(
        self,
        *,
        tier: ModelTier,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: Literal["none", "low", "high", "max"] = "none",
        strict: bool = False,
        structured_transport: Literal["chat", "responses"] = "responses",
        timeout_seconds: float = 30.0,
        fallback_api_keys: Sequence[str] = (),
        key_health: ProviderKeyHealthRegistry | None = None,
        key_rotation_enabled: bool = False,
        auto_quarantine_enabled: bool = False,
        client: OpenAI | None = None,
    ) -> None:
        if tier is ModelTier.M1:
            raise ValueError("M1 uses DashScopeEmbeddingClient")
        self.tier = tier
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.strict = strict
        self.structured_transport = structured_transport
        self._key_health = key_health or DEFAULT_KEY_HEALTH
        self._key_rotation_enabled = key_rotation_enabled
        self._auto_quarantine_enabled = auto_quarantine_enabled
        self._api_keys: tuple[str, ...]
        self._clients: tuple[OpenAI, ...]
        self._async_clients: tuple[AsyncOpenAI, ...]
        if client is not None:
            self._api_keys = ("injected",)
            self._clients = (client,)
            self._async_clients = ()
        else:
            keys = [api_key, *(key for key in fallback_api_keys if key and key != api_key)]
            self._api_keys = tuple(dict.fromkeys(keys))
            self._clients = tuple(
                OpenAI(
                    api_key=key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                    max_retries=0,
                )
                for key in dict.fromkeys(keys)
            )
            self._async_clients = tuple(
                AsyncOpenAI(
                    api_key=key,
                    base_url=base_url,
                    timeout=timeout_seconds,
                    max_retries=0,
                )
                for key in dict.fromkeys(keys)
            )

    def _sync_pairs(self) -> tuple[tuple[str, OpenAI], ...]:
        pairs = tuple(zip(self._api_keys, self._clients, strict=True))
        return pairs if self._key_rotation_enabled else pairs[:1]

    def _async_pairs(self) -> tuple[tuple[str, AsyncOpenAI], ...]:
        pairs = tuple(zip(self._api_keys, self._async_clients, strict=True))
        return pairs if self._key_rotation_enabled else pairs[:1]

    def _key_available(self, key: str) -> bool:
        return (
            not self._auto_quarantine_enabled or key == "injected" or self._key_health.healthy(key)
        )

    def _record_key_success(self, key: str) -> None:
        if self._auto_quarantine_enabled and key != "injected":
            self._key_health.record_success(key)

    def _record_key_failure(self, key: str, exc: Exception) -> None:
        if self._auto_quarantine_enabled and key != "injected":
            self._key_health.record_failure(key, classify_provider_error(exc))

    async def acomplete(self, request: StructuredModelRequest) -> StructuredModelResult:
        """Native async equivalent used by the BULK_EPOCH stage executor."""

        if not self._async_clients:
            raise RuntimeError("async DashScope client is unavailable for an injected sync client")
        started = perf_counter()
        effective_strict = self.strict or request.strict or request.output_mode == "json_schema"
        last_error: Exception | None = None
        last_attempted_key: str | None = None
        provider_response: Any | None = None
        selected_key: str | None = None
        pairs = self._async_pairs()
        for index, (key, client) in enumerate(pairs):
            if not self._key_available(key):
                continue
            try:
                last_attempted_key = key
                reasoning_effort = (
                    request.reasoning_effort
                    if request.reasoning_effort != "none"
                    else self.reasoning_effort
                )
                if effective_strict or self.structured_transport == "chat":
                    from cdecr.usage_capture import acall

                    provider_response = await acall(
                        client.chat.completions.create,
                        _usage_provider="bailian",
                        _usage_node=request.schema_name,
                        **_structured_chat_kwargs(
                            model=self.model,
                            request=request,
                            strict=effective_strict,
                            reasoning_effort=reasoning_effort,
                        ),
                    )
                else:
                    from cdecr.usage_capture import acall

                    provider_response = await acall(
                        client.responses.create,
                        _usage_provider="bailian",
                        _usage_node=request.schema_name,
                        **_responses_kwargs(
                            model=self.model,
                            request=ResponsesModelRequest(
                                input=[
                                    {"role": "system", "content": request.system_prompt},
                                    {"role": "user", "content": request.user_prompt},
                                ],
                                json_schema=request.json_schema,
                                output_mode="json_object",
                                schema_name=request.schema_name,
                                strict=False,
                                reasoning_effort=reasoning_effort,
                                session_cache=request.session_cache,
                                metadata=request.metadata,
                            ),
                            session_cache_header=True,
                        ),
                    )
                self._record_key_success(key)
                selected_key = key
                break
            except Exception as exc:
                last_error = exc
                self._record_key_failure(key, exc)
                if index == len(pairs) - 1 or not _should_rotate_key(exc):
                    break
        if provider_response is None:
            if last_error is None:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="provider_all_keys_unavailable",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            raise _safe_model_error(
                last_error,
                self.tier,
                started_at=started,
                provider_key=last_attempted_key,
            ) from last_error
        used_chat = effective_strict or self.structured_transport == "chat"
        if not used_chat:
            text = getattr(provider_response, "output_text", None)
        else:
            text = provider_response.choices[0].message.content
        usage = getattr(provider_response, "usage", None)
        request_id = getattr(provider_response, "_request_id", None)
        input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
        return _structured_result_from_text(
            tier=self.tier,
            model=self.model,
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=None,
            request_id=request_id,
            started_at=started,
            transport=(
                "chat_json_schema"
                if effective_strict
                else "chat_json_object"
                if used_chat
                else "responses_json_object"
            ),
            output_mode="json_schema" if effective_strict else "json_object",
            effective_reasoning_effort=(
                reasoning_effort if effective_strict or not used_chat else "none"
            ),
            provider_key_fingerprint=_selected_key_fingerprint(selected_key),
        )

    def complete_response(self, request: ResponsesModelRequest) -> StructuredModelResult:
        """Run one structured Responses turn, optionally continuing a prior response."""

        started = perf_counter()
        last_error: Exception | None = None
        last_attempted_key: str | None = None
        provider_response: Any | None = None
        selected_key: str | None = None
        pairs = self._sync_pairs()
        for index, (key, client) in enumerate(pairs):
            if not self._key_available(key):
                continue
            try:
                last_attempted_key = key
                if request.output_mode == "json_schema" or request.strict:
                    from cdecr.usage_capture import call

                    provider_response = call(
                        client.responses.create,
                        _usage_provider="bailian",
                        _usage_node=request.schema_name,
                        **_responses_json_schema_kwargs(model=self.model, request=request),
                    )
                else:
                    from cdecr.usage_capture import call

                    provider_response = call(
                        client.responses.create,
                        _usage_provider="bailian",
                        _usage_node=request.schema_name,
                        **_responses_kwargs(
                            model=self.model,
                            request=request,
                            session_cache_header=True,
                        ),
                    )
                self._record_key_success(key)
                selected_key = key
                break
            except Exception as exc:
                last_error = exc
                self._record_key_failure(key, exc)
                if index == len(pairs) - 1 or not _should_rotate_key(exc):
                    break
        if provider_response is None:
            if last_error is None:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="provider_all_keys_unavailable",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            raise _safe_model_error(
                last_error,
                self.tier,
                started_at=started,
                provider_key=last_attempted_key,
            ) from last_error
        usage = getattr(provider_response, "usage", None)
        if request.output_mode == "json_schema" or request.strict:
            text = getattr(provider_response, "output_text", None)
            input_tokens = _usage_value(usage, "input_tokens", "prompt_tokens")
            output_tokens = _usage_value(usage, "output_tokens", "completion_tokens")
            response_id = getattr(provider_response, "id", None) or getattr(
                provider_response, "_request_id", None
            )
        else:
            text = getattr(provider_response, "output_text", None)
            input_tokens = _usage_value(usage, "input_tokens", "prompt_tokens")
            output_tokens = _usage_value(usage, "output_tokens", "completion_tokens")
            response_id = getattr(provider_response, "id", None)
        normalized_payload = _response_payload_for_request(text, request)
        return _structured_result_from_text(
            tier=self.tier,
            model=self.model,
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=_reasoning_usage_value(usage),
            request_id=getattr(provider_response, "_request_id", None),
            started_at=started,
            cached_input_tokens=_cached_input_usage_value(usage),
            response_id=response_id,
            payload_override=normalized_payload,
            transport=(
                "responses_json_schema"
                if request.output_mode == "json_schema" or request.strict
                else "responses_json_object"
            ),
            output_mode=request.output_mode,
            effective_reasoning_effort=request.reasoning_effort,
            provider_key_fingerprint=_selected_key_fingerprint(selected_key),
        )

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        started = perf_counter()
        effective_strict = self.strict or request.strict or request.output_mode == "json_schema"
        last_error: Exception | None = None
        last_attempted_key: str | None = None
        provider_response: Any | None = None
        selected_key: str | None = None
        pairs = self._sync_pairs()
        for index, (key, client) in enumerate(pairs):
            if not self._key_available(key):
                continue
            try:
                last_attempted_key = key
                if not effective_strict and self.structured_transport == "responses":
                    from cdecr.usage_capture import call

                    provider_response = call(
                        client.responses.create,
                        _usage_provider="bailian",
                        _usage_node=request.schema_name,
                        **_responses_kwargs(
                            model=self.model,
                            request=ResponsesModelRequest(
                                input=[
                                    {"role": "system", "content": request.system_prompt},
                                    {"role": "user", "content": request.user_prompt},
                                ],
                                json_schema=request.json_schema,
                                output_mode="json_object",
                                schema_name=request.schema_name,
                                strict=False,
                                reasoning_effort=(
                                    request.reasoning_effort
                                    if request.reasoning_effort != "none"
                                    else self.reasoning_effort
                                ),
                                metadata=request.metadata,
                            ),
                            session_cache_header=True,
                        ),
                    )
                else:
                    from cdecr.usage_capture import call

                    provider_response = call(
                        client.chat.completions.create,
                        _usage_provider="bailian",
                        _usage_node=request.schema_name,
                        **_structured_chat_kwargs(
                            model=self.model,
                            request=request,
                            strict=effective_strict,
                            reasoning_effort=(
                                request.reasoning_effort
                                if request.reasoning_effort != "none"
                                else self.reasoning_effort
                            ),
                        ),
                    )
                self._record_key_success(key)
                selected_key = key
                break
            except Exception as exc:
                last_error = exc
                self._record_key_failure(key, exc)
                if index == len(pairs) - 1 or not _should_rotate_key(exc):
                    break
        if provider_response is None:
            if last_error is None:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="provider_all_keys_unavailable",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            raise _safe_model_error(
                last_error,
                self.tier,
                started_at=started,
                provider_key=last_attempted_key,
            ) from last_error

        used_chat = effective_strict or self.structured_transport == "chat"
        text = (
            provider_response.choices[0].message.content
            if used_chat
            else getattr(provider_response, "output_text", None)
        )
        usage = getattr(provider_response, "usage", None)
        request_id = getattr(provider_response, "_request_id", None)
        input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")

        effective_reasoning = (
            request.reasoning_effort
            if request.reasoning_effort != "none"
            else self.reasoning_effort
        )
        return _structured_result_from_text(
            tier=self.tier,
            model=self.model,
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=_reasoning_usage_value(usage),
            request_id=request_id,
            started_at=started,
            transport=(
                "chat_json_schema"
                if effective_strict
                else "chat_json_object"
                if used_chat
                else "responses_json_object"
            ),
            output_mode="json_schema" if effective_strict else "json_object",
            effective_reasoning_effort=(
                effective_reasoning if effective_strict or not used_chat else "none"
            ),
            provider_key_fingerprint=_selected_key_fingerprint(selected_key),
        )


def deepseek_strict_wire_schema(schema: object) -> dict[str, object]:
    """Compile a provider-only strict schema without mutating the CDECR DTO schema."""

    value = compact_wire_schema(schema)
    if not isinstance(value, dict):
        raise ValueError("structured output schema must be an object")
    root = value
    unsupported = {"minLength", "maxLength", "minItems", "maxItems"}

    def resolve_local_ref(ref: str) -> object:
        resolved: object = root
        for part in ref[2:].split("/"):
            if not isinstance(resolved, dict):
                return {"$ref": ref}
            resolved = resolved.get(part.replace("~1", "/").replace("~0", "~"), {})
        return json.loads(json.dumps(resolved))

    def expand_refs(item: object, stack: tuple[str, ...] = ()) -> object:
        if isinstance(item, list):
            return [expand_refs(child, stack) for child in item]
        if not isinstance(item, dict):
            return item
        ref = item.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if ref in stack:
                raise ValueError(f"recursive local schema reference is unsupported: {ref}")
            resolved = resolve_local_ref(ref)
            if not isinstance(resolved, dict):
                raise ValueError(f"invalid local schema reference: {ref}")
            merged = {**resolved, **{key: child for key, child in item.items() if key != "$ref"}}
            return expand_refs(merged, (*stack, ref))
        return {key: expand_refs(child, stack) for key, child in item.items()}

    value = expand_refs(value)
    if not isinstance(value, dict):
        raise ValueError("structured output schema must remain an object")
    value.pop("$defs", None)

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key in unsupported:
                item.pop(key, None)
            properties = item.get("properties")
            if item.get("type") == "object" and isinstance(properties, dict):
                item["required"] = list(properties)
                item["additionalProperties"] = False
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return value


class DeepSeekStructuredModelClient:
    """Official DeepSeek adapter with opt-in strict tools."""

    def __init__(
        self,
        *,
        tier: ModelTier,
        api_key: str,
        base_url: str,
        model: str = "deepseek-v4-flash",
        reasoning_effort: Literal["none", "low", "high", "max"],
        strict: bool = False,
        timeout_seconds: float = 600.0,
        client: OpenAI | None = None,
        async_client: AsyncOpenAI | None = None,
    ) -> None:
        if tier not in {ModelTier.M2, ModelTier.M3, ModelTier.M4}:
            raise ValueError("DeepSeek official provider is supported only for M2/M3/M4")
        self.tier = tier
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.strict = strict
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._async_client = async_client or (
            None
            if client is not None
            else AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
        )

    def _thinking_kwargs(self) -> dict[str, Any]:
        return _deepseek_thinking_kwargs(self.reasoning_effort)

    async def acomplete(self, request: StructuredModelRequest) -> StructuredModelResult:
        """Use one shared native async transport for bulk stage requests."""

        if self._async_client is None:
            raise RuntimeError("async DeepSeek client is unavailable for an injected sync client")
        started = perf_counter()
        system_prompt = request.system_prompt
        if self.strict:
            system_prompt = (
                f"{system_prompt}\nYou must call {DEEPSEEK_TOOL_NAME} exactly once and return "
                "the complete result as its arguments."
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **self._thinking_kwargs(),
        }
        if self.strict:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": DEEPSEEK_TOOL_NAME,
                        "description": (
                            "Always call this function exactly once to return the complete "
                            "structured CDECR result."
                        ),
                        "strict": True,
                        "parameters": deepseek_strict_wire_schema(request.json_schema),
                    },
                }
            ]
        else:
            schema = json.dumps(
                compact_wire_schema(request.json_schema),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            messages[0]["content"] = (
                f"{request.system_prompt}\nReturn exactly one valid JSON object without Markdown."
            )
            messages[1]["content"] = (
                f"{request.user_prompt}\nReturn JSON matching this schema: {schema}"
            )
            kwargs["response_format"] = {"type": "json_object"}
        try:
            from cdecr.usage_capture import acall

            response = await acall(
                self._async_client.chat.completions.create,
                _usage_provider="deepseek",
                _usage_node=request.schema_name,
                **kwargs,
            )
        except Exception as exc:
            raise _safe_model_error(exc, self.tier, started_at=started) from exc
        message = response.choices[0].message
        if self.strict:
            tool_calls = getattr(message, "tool_calls", None) or []
            if len(tool_calls) != 1:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="invalid_tool_call_count",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            function = tool_calls[0].function
            if function.name != DEEPSEEK_TOOL_NAME:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="invalid_tool_name",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            text = function.arguments
        else:
            text = message.content
        usage = getattr(response, "usage", None)
        return _structured_result_from_text(
            tier=self.tier,
            model=self.model,
            text=text,
            input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
            output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
            reasoning_tokens=_reasoning_usage_value(usage),
            request_id=getattr(response, "_request_id", None),
            started_at=started,
        )

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        started = perf_counter()
        system_prompt = request.system_prompt
        if self.strict:
            system_prompt = (
                f"{system_prompt}\nYou must call {DEEPSEEK_TOOL_NAME} exactly once and return "
                "the complete result as its arguments."
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **self._thinking_kwargs(),
        }
        if self.strict:
            kwargs.update(
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": DEEPSEEK_TOOL_NAME,
                            "description": (
                                "Always call this function exactly once to return the complete "
                                "structured CDECR result."
                            ),
                            "strict": True,
                            "parameters": deepseek_strict_wire_schema(request.json_schema),
                        },
                    }
                ],
            )
        else:
            schema = json.dumps(
                compact_wire_schema(request.json_schema),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            messages[0]["content"] = (
                f"{request.system_prompt}\nReturn exactly one valid JSON object without Markdown."
            )
            messages[1]["content"] = (
                f"{request.user_prompt}\nReturn JSON matching this schema: {schema}"
            )
            kwargs["response_format"] = {"type": "json_object"}
        try:
            from cdecr.usage_capture import call

            response = call(
                self._client.chat.completions.create,
                _usage_provider="deepseek",
                _usage_node=request.schema_name,
                **kwargs,
            )
        except Exception as exc:
            raise _safe_model_error(exc, self.tier, started_at=started) from exc

        message = response.choices[0].message
        if self.strict:
            tool_calls = getattr(message, "tool_calls", None) or []
            if len(tool_calls) != 1:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="invalid_tool_call_count",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            function = tool_calls[0].function
            if function.name != DEEPSEEK_TOOL_NAME:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="invalid_tool_name",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            text = function.arguments
        else:
            text = message.content
        usage = getattr(response, "usage", None)
        input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
        reasoning_tokens = _reasoning_usage_value(usage)
        if not isinstance(text, str) or not text.strip():
            raise ModelAdapterError(
                tier=self.tier,
                code="empty_response",
                latency_ms=round((perf_counter() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
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
            )
        return StructuredModelResult(
            model=self.model,
            payload=payload,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=round((perf_counter() - started) * 1000),
            request_id=getattr(response, "_request_id", None),
        )

    def complete_response(self, request: ResponsesModelRequest) -> StructuredModelResult:
        """Translate the local Responses contract onto DeepSeek Chat Completions.

        DeepSeek's documented OpenAI-compatible surface is Chat Completions, not
        OpenAI Responses.  CDECR still presents one internal Responses contract to
        callers, while this adapter preserves the JSON/schema and reasoning settings
        on the provider's supported wire format.
        """

        if request.previous_response_id is not None:
            raise ModelAdapterError(
                tier=self.tier,
                code="deepseek_previous_response_unsupported",
                latency_ms=0,
            )
        started = perf_counter()
        effective_strict = request.output_mode == "json_schema" or request.strict
        messages = (
            copy.deepcopy(request.input)
            if effective_strict
            else _responses_input_with_schema(request)
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **_deepseek_thinking_kwargs(request.reasoning_effort),
        }
        if effective_strict:
            instruction = (
                f"You must call {DEEPSEEK_TOOL_NAME} exactly once and return the complete "
                "JSON result as its arguments."
            )
            for item in messages:
                if item.get("role") == "system" and isinstance(item.get("content"), str):
                    item["content"] = f"{item['content']}\n{instruction}"
                    break
            else:
                messages.insert(0, {"role": "system", "content": instruction})
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": DEEPSEEK_TOOL_NAME,
                        "description": (
                            "Always call this function exactly once to return the complete "
                            "structured CDECR result."
                        ),
                        "strict": True,
                        "parameters": deepseek_strict_wire_schema(request.json_schema),
                    },
                }
            ]
        else:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            from cdecr.usage_capture import call

            response = call(
                self._client.chat.completions.create,
                _usage_provider="deepseek",
                _usage_node=request.schema_name,
                **kwargs,
            )
        except Exception as exc:
            raise _safe_model_error(exc, self.tier, started_at=started) from exc
        message = response.choices[0].message
        if effective_strict:
            tool_calls = getattr(message, "tool_calls", None) or []
            if len(tool_calls) != 1:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="invalid_tool_call_count",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            function = tool_calls[0].function
            if function.name != DEEPSEEK_TOOL_NAME:
                raise ModelAdapterError(
                    tier=self.tier,
                    code="invalid_tool_name",
                    latency_ms=round((perf_counter() - started) * 1000),
                )
            text = function.arguments
        else:
            text = message.content
        usage = getattr(response, "usage", None)
        return _structured_result_from_text(
            tier=self.tier,
            model=self.model,
            text=text,
            input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
            output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
            reasoning_tokens=_reasoning_usage_value(usage),
            request_id=getattr(response, "_request_id", None),
            started_at=started,
            transport="chat_json_schema" if effective_strict else "chat_json_object",
            output_mode="json_schema" if effective_strict else "json_object",
            effective_reasoning_effort=request.reasoning_effort,
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
    reasoning_efforts: Mapping[ModelTier, Literal["none", "low", "high", "max"]] | None = None,
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
            reasoning_effort=(reasoning_efforts or {}).get(tier, "none"),
            strict=True,
            timeout_seconds=timeout_seconds,
            fallback_api_keys=fallback_api_keys,
        )
        request = StructuredModelRequest(
            system_prompt="You are a deterministic API health probe.",
            user_prompt=f"Return ok=true, tier={tier.value}, and value=1.",
            json_schema=ProbePayload.model_json_schema(),
            output_mode="json_schema",
            schema_name=f"cdecr_probe_{tier.value}",
            strict=True,
            reasoning_effort=(reasoning_efforts or {}).get(tier, "none"),
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
