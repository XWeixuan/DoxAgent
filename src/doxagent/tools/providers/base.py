"""Shared real-tool provider primitives."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx

from doxagent.models import ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

JsonObject = dict[str, Any]
HttpParamValue = str | int | float | bool | None

DEFAULT_USER_AGENT = "DoxAgent/0.1 contact@example.com"


class TTLCache:
    """Small in-memory TTL cache used only inside a process."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.time():
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        self._items[key] = (time.time() + ttl_seconds, value)

class BaseRealToolClient:
    _rate_limit_lock = threading.Lock()
    _rate_limit_last_request_at: dict[str, float] = {}

    def __init__(
        self,
        settings: DoxAgentSettings,
        cache: TTLCache | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache or TTLCache()
        self.client = client or httpx.Client(timeout=settings.tool_http_timeout_seconds)

    def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl: int | None = None,
        rate_limit_key: str | None = None,
        min_interval_seconds: float | None = None,
        max_rate_limit_retries: int = 0,
    ) -> JsonObject:
        cache_key = _cache_key("GET", url, params or {}, {})
        if cache_ttl:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cast(JsonObject, cached)
        response = self._send_with_rate_limit(
            lambda: self.client.get(url, params=_to_httpx_params(params), headers=headers),
            rate_limit_key=rate_limit_key,
            min_interval_seconds=min_interval_seconds,
            max_rate_limit_retries=max_rate_limit_retries,
        )
        data = _json_object(response.json())
        if cache_ttl:
            self.cache.set(cache_key, data, cache_ttl)
        return data

    def _post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, object],
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl: int | None = None,
        rate_limit_key: str | None = None,
        min_interval_seconds: float | None = None,
        max_rate_limit_retries: int = 0,
    ) -> JsonObject:
        cache_key = _cache_key("POST", url, params or {}, json_body)
        if cache_ttl:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cast(JsonObject, cached)
        response = self._send_with_rate_limit(
            lambda: self.client.post(
                url,
                params=_to_httpx_params(params),
                json=json_body,
                headers=headers,
            ),
            rate_limit_key=rate_limit_key,
            min_interval_seconds=min_interval_seconds,
            max_rate_limit_retries=max_rate_limit_retries,
        )
        data = _json_object(response.json())
        if cache_ttl:
            self.cache.set(cache_key, data, cache_ttl)
        return data

    def _get_text(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl: int | None = None,
        rate_limit_key: str | None = None,
        min_interval_seconds: float | None = None,
        max_rate_limit_retries: int = 0,
    ) -> str:
        cache_key = _cache_key("GET_TEXT", url, params or {}, {})
        if cache_ttl:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cast(str, cached)
        response = self._send_with_rate_limit(
            lambda: self.client.get(url, params=_to_httpx_params(params), headers=headers),
            rate_limit_key=rate_limit_key,
            min_interval_seconds=min_interval_seconds,
            max_rate_limit_retries=max_rate_limit_retries,
        )
        text = response.text
        if cache_ttl:
            self.cache.set(cache_key, text, cache_ttl)
        return text

    def _success(
        self,
        request: ToolRequest,
        *,
        output: JsonObject,
        raw: object | None,
        source_kind: str,
        source_id: str,
        title: str,
        summary: str,
        source_scope: str,
        confidence: float,
        metadata: Mapping[str, object],
    ) -> ToolResult:
        enriched_output = dict(output)
        enriched_output.setdefault(
            "source_coordinates",
            {
                "source_kind": source_kind,
                "source_id": source_id,
                "title": title,
                "source_scope": source_scope,
                "confidence": confidence,
                "tool_name": request.tool_name,
                "provider": source_id.split(":", 1)[0],
                **dict(metadata),
            },
        )
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output=enriched_output,
            output_summary=summary,
            raw=raw,
        )

    def _failure(
        self,
        request: ToolRequest,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.FAILED,
            error=ToolError(
                code=code,
                message=message,
                retryable=retryable,
                details=dict(details or {}),
            ),
        )

    def _handle_exception(self, request: ToolRequest, exc: Exception) -> ToolResult:
        if isinstance(exc, ProviderHttpError):
            return self._failure(
                request,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
        if isinstance(exc, httpx.RequestError):
            return self._failure(
                request,
                code="upstream_unavailable",
                message=str(exc),
                retryable=True,
                details={"provider_error": type(exc).__name__},
            )
        return self._failure(
            request,
            code="tool_execution_failed",
            message=str(exc),
            retryable=False,
            details={"provider_error": type(exc).__name__},
        )

    def _partial(
        self,
        request: ToolRequest,
        *,
        output: JsonObject,
        raw: object | None,
        source_kind: str,
        source_id: str,
        title: str,
        summary: str,
        source_scope: str,
        confidence: float,
        metadata: Mapping[str, object],
        code: str,
        message: str,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> ToolResult:
        """Return usable data while preserving an incomplete-provider semantic."""

        enriched_output = dict(output)
        enriched_output.setdefault(
            "source_coordinates",
            {
                "source_kind": source_kind,
                "source_id": source_id,
                "title": title,
                "source_scope": source_scope,
                "confidence": confidence,
                "tool_name": request.tool_name,
                "provider": source_id.split(":", 1)[0],
                **dict(metadata),
            },
        )
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.PARTIAL,
            output=enriched_output,
            output_summary=summary,
            raw=raw,
            error=ToolError(
                code=code,
                message=message,
                retryable=retryable,
                details=dict(details or {}),
            ),
        )

    def _send_with_rate_limit(
        self,
        send: Callable[[], httpx.Response],
        *,
        rate_limit_key: str | None,
        min_interval_seconds: float | None,
        max_rate_limit_retries: int,
    ) -> httpx.Response:
        attempts = max(0, max_rate_limit_retries) + 1
        for attempt in range(attempts):
            if rate_limit_key and min_interval_seconds and min_interval_seconds > 0:
                self._wait_for_rate_limit(rate_limit_key, min_interval_seconds)
            response = send()
            try:
                _raise_for_status(response)
                return response
            except ProviderHttpError as exc:
                if exc.code != "rate_limited" or attempt >= attempts - 1:
                    raise
                retry_after = _retry_after_seconds(exc.details.get("retry_after"))
                time.sleep(retry_after or max(min_interval_seconds or 0.5, 0.5))
        raise AssertionError("unreachable rate-limit retry loop")

    @classmethod
    def _wait_for_rate_limit(cls, key: str, min_interval_seconds: float) -> None:
        with cls._rate_limit_lock:
            now = time.monotonic()
            last = cls._rate_limit_last_request_at.get(key)
            if last is not None:
                wait_seconds = min_interval_seconds - (now - last)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                    now = time.monotonic()
            cls._rate_limit_last_request_at[key] = now


class BoundToolClient:
    def __init__(self, call_func: Callable[[ToolRequest], ToolResult]) -> None:
        self._call_func = call_func

    def call(self, request: ToolRequest) -> ToolResult:
        return self._call_func(request)
class ProviderHttpError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    code = {
        401: "auth_failed",
        403: "entitlement_or_permission_denied",
        404: "not_found",
        429: "rate_limited",
    }.get(response.status_code, "upstream_http_error")
    raise ProviderHttpError(
        code=code,
        message=f"Upstream returned HTTP {response.status_code}.",
        retryable=response.status_code in {408, 429, 500, 502, 503, 504},
        details={
            "status_code": response.status_code,
            "retry_after": response.headers.get("Retry-After"),
            "body_preview": response.text[:500],
        },
    )


def _retry_after_seconds(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _json_object(value: object) -> JsonObject:
    if isinstance(value, dict):
        return cast(JsonObject, value)
    if isinstance(value, list):
        return {"items": value}
    return {"value": value}


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _input_str(request: ToolRequest, key: str, default: str) -> str:
    value = request.input.get(key, default)
    if value is None:
        return default
    return str(value)


def _input_str_any(request: ToolRequest, keys: tuple[str, ...], default: str) -> str:
    for key in keys:
        value = request.input.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return default


def _input_list(request: ToolRequest, key: str) -> list[str]:
    value = request.input.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str) and value:
        return [value]
    return []


def _strip_none(input_data: Mapping[str, object]) -> JsonObject:
    return {key: value for key, value in input_data.items() if value is not None}


def _validate_single_scope_id(input_data: Mapping[str, object]) -> None:
    keys = ["narrative_event_id", "narrative_id", "proposition_id"]
    present = [key for key in keys if input_data.get(key)]
    if len(present) != 1:
        raise ValueError(
            "Exactly one of narrative_event_id, narrative_id, proposition_id is required."
        )


def _require(value: str | None, env_name: str) -> str:
    if not value:
        raise ValueError(f"{env_name} is required for this tool.")
    return value


def _normalize_cik(raw_cik: str) -> str:
    digits = re.sub(r"\D", "", raw_cik)
    if not digits:
        raise ValueError("CIK must contain digits.")
    return digits.zfill(10)


def _cache_key(
    method: str, url: str, params: Mapping[str, object], body: Mapping[str, object]
) -> str:
    payload = json.dumps(
        {"method": method, "url": url, "params": dict(params), "body": dict(body)},
        sort_keys=True,
        default=str,
    )
    return payload


def _to_httpx_params(params: Mapping[str, object] | None) -> dict[str, HttpParamValue] | None:
    if params is None:
        return None
    normalized: dict[str, HttpParamValue] = {}
    for key, value in params.items():
        if isinstance(value, str | int | float | bool) or value is None:
            normalized[key] = value
        else:
            normalized[key] = str(value)
    return normalized
