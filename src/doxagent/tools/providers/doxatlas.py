"""DoxAtlas HTTP tool provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    BoundToolClient,
    JsonObject,
    _strip_none,
    _validate_single_scope_id,
)
from doxagent.tools.schema import ToolRequest, ToolResult

_SCOPE_ID_FIELDS = frozenset({"narrative_event_id", "narrative_id", "proposition_id"})


@dataclass(frozen=True)
class EndpointSpec:
    endpoint: str
    evidence_source_type: EvidenceSourceType
    citation_scope: str
    title: str
    summary: str
    allowed_fields: frozenset[str]
    required_fields: frozenset[str] = frozenset()
    requires_ticker: bool = False
    single_scope_id: bool = False
    cacheable: bool = True
    numeric_ranges: dict[str, tuple[int, int]] | None = None


DOXATLAS_TOOL_SPECS: dict[str, EndpointSpec] = {
    "doxa_run_narrative_research": EndpointSpec(
        "run-narrative-research",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_run",
        "DoxAtlas Narrative Research run",
        "DoxAtlas Narrative Research run was requested.",
        frozenset({"ticker", "language", "force"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        cacheable=False,
    ),
    "doxa_run_analysis": EndpointSpec(
        "run-analysis",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_run",
        "DoxAtlas analysis task",
        "DoxAtlas single-ticker analysis task was requested.",
        frozenset({"ticker", "language", "reuse_recent"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        cacheable=False,
    ),
    "doxa_get_narrative_report": EndpointSpec(
        "get-narrative-report",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_narrative_report",
        "DoxAtlas narrative report",
        "DoxAtlas narrative report was retrieved.",
        frozenset({"ticker", "run_id"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
    ),
    "doxa_get_analysis": EndpointSpec(
        "get-analysis",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_analysis",
        "DoxAtlas analysis",
        "DoxAtlas single-ticker analysis was retrieved.",
        frozenset({"ticker", "task_id", "capsule_limit"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        numeric_ranges={"capsule_limit": (1, 20)},
    ),
    "doxa_query_propositions": EndpointSpec(
        "query-propositions",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_propositions",
        "DoxAtlas propositions",
        "DoxAtlas propositions were retrieved.",
        _SCOPE_ID_FIELDS,
        single_scope_id=True,
    ),
    "doxa_get_ignored_propositions": EndpointSpec(
        "get-ignored-propositions",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_ignored_propositions",
        "DoxAtlas ignored propositions",
        "DoxAtlas ignored propositions were retrieved.",
        frozenset({"narrative_id"}),
        required_fields=frozenset({"narrative_id"}),
    ),
    "doxa_get_social_result": EndpointSpec(
        "get-social-result",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_social_result",
        "DoxAtlas social result",
        "DoxAtlas social result was retrieved.",
        _SCOPE_ID_FIELDS,
        single_scope_id=True,
    ),
    "doxa_get_media_result": EndpointSpec(
        "get-media-result",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_media_result",
        "DoxAtlas media result",
        "DoxAtlas media result was retrieved.",
        _SCOPE_ID_FIELDS,
        single_scope_id=True,
    ),
    "doxa_get_event_source": EndpointSpec(
        "get-event-source",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_event_source",
        "DoxAtlas event source",
        "DoxAtlas event source was retrieved.",
        frozenset({"narrative_event_id", "limit"}),
        required_fields=frozenset({"narrative_event_id"}),
        numeric_ranges={"limit": (1, 20)},
    ),
}

DOXATLAS_ALIASES = {
    "doxatlas.query": "doxa_get_narrative_report",
    "doxatlas.source_lookup": "doxa_get_event_source",
}


class DoxAtlasToolClient(BaseRealToolClient):
    def for_tool(self, tool_name: str) -> BoundToolClient:
        return BoundToolClient(lambda request: self._call_doxatlas(tool_name, request))

    def _call_doxatlas(self, tool_name: str, request: ToolRequest) -> ToolResult:
        resolved_name = DOXATLAS_ALIASES.get(tool_name, tool_name)
        spec = DOXATLAS_TOOL_SPECS[resolved_name]
        try:
            if (
                not self.settings.doxatlas_tool_base_url
                or not self.settings.doxatlas_tool_server_token
            ):
                return self._failure(
                    request,
                    code="provider_not_configured",
                    message="DOXATLAS_TOOL_BASE_URL and DOXATLAS_TOOL_SERVER_TOKEN are required.",
                )
            payload = self._build_payload(request, spec)
            url = f"{self.settings.doxatlas_tool_base_url.rstrip('/')}/{spec.endpoint}"
            raw = self._post_doxatlas_json(
                url,
                json_body=payload,
                headers={"Authorization": f"Bearer {self.settings.doxatlas_tool_server_token}"},
                cache_ttl=self.settings.doxatlas_cache_ttl_seconds
                if spec.cacheable
                else None,
            )
            return self._success(
                request,
                output={"provider": "doxatlas", "data": raw},
                raw=raw,
                source_type=spec.evidence_source_type,
                source_id=f"doxatlas:{spec.endpoint}:{request.ticker}",
                title=spec.title,
                summary=spec.summary,
                citation_scope=spec.citation_scope,
                confidence=0.8,
                metadata={
                    "endpoint": spec.endpoint,
                    "alias_for": resolved_name,
                    "http_method": "POST",
                    "tool_server_base_url": self.settings.doxatlas_tool_base_url.rstrip("/"),
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _build_payload(self, request: ToolRequest, spec: EndpointSpec) -> JsonObject:
        if "user_id" in request.input:
            raise ValueError("user_id must not be passed to DoxAtlas tools.")
        unknown_fields = set(request.input) - spec.allowed_fields
        if unknown_fields:
            fields = ", ".join(sorted(unknown_fields))
            raise ValueError(f"Unsupported DoxAtlas tool input field(s): {fields}.")

        payload = _strip_none(request.input)
        if spec.requires_ticker and "ticker" not in payload:
            payload["ticker"] = request.ticker
        if spec.single_scope_id:
            _validate_single_scope_id(payload)

        missing_fields = [field for field in spec.required_fields if not payload.get(field)]
        if missing_fields:
            fields = ", ".join(sorted(missing_fields))
            raise ValueError(f"Missing required DoxAtlas tool input field(s): {fields}.")

        for field, bounds in (spec.numeric_ranges or {}).items():
            if field not in payload:
                continue
            value = payload[field]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{field} must be an integer.")
            minimum, maximum = bounds
            if value < minimum or value > maximum:
                raise ValueError(f"{field} must be between {minimum} and {maximum}.")
        return payload

    def _post_doxatlas_json(
        self,
        url: str,
        *,
        json_body: JsonObject,
        headers: dict[str, str],
        cache_ttl: int | None,
    ) -> JsonObject:
        cached = None
        cache_key = ""
        if cache_ttl:
            cache_key = f"DOXATLAS_POST:{url}:{json_body}"
            cached = self.cache.get(cache_key)
        if cached is not None:
            return cached if isinstance(cached, dict) else {"value": cached}

        response = self.client.post(url, json=json_body, headers=headers)
        try:
            data: Any = response.json()
        except ValueError:
            data = {"value": response.text}

        if response.status_code >= 400 or _has_error_envelope(data):
            error = data.get("error") if isinstance(data, dict) else None
            provider_code = (
                str(error.get("code"))
                if isinstance(error, dict) and error.get("code") is not None
                else "UPSTREAM_HTTP_ERROR"
            )
            message = (
                str(error.get("message"))
                if isinstance(error, dict) and error.get("message") is not None
                else f"DoxAtlas returned HTTP {response.status_code}."
            )
            details = error.get("details") if isinstance(error, dict) else None
            raise DoxAtlasToolServerError(
                provider_code=provider_code,
                message=message,
                status_code=response.status_code,
                details=details,
            )

        raw = data if isinstance(data, dict) else {"value": data}
        if cache_ttl:
            self.cache.set(cache_key, raw, cache_ttl)
        return raw

    def _handle_exception(self, request: ToolRequest, exc: Exception) -> ToolResult:
        if isinstance(exc, DoxAtlasToolServerError):
            return self._failure(
                request,
                code=_normalize_doxatlas_error_code(exc.provider_code),
                message=exc.message,
                retryable=exc.retryable,
                details={
                    "provider_code": exc.provider_code,
                    "status_code": exc.status_code,
                    "provider_details": exc.details,
                },
            )
        return super()._handle_exception(request, exc)


class DoxAtlasToolServerError(Exception):
    def __init__(
        self,
        *,
        provider_code: str,
        message: str,
        status_code: int,
        details: object,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.message = message
        self.status_code = status_code
        self.details = details
        self.retryable = _is_retryable_doxatlas_error(provider_code, status_code)


def _has_error_envelope(data: object) -> bool:
    return isinstance(data, dict) and isinstance(data.get("error"), dict)


def _normalize_doxatlas_error_code(provider_code: str) -> str:
    return provider_code.lower()


def _is_retryable_doxatlas_error(provider_code: str, status_code: int) -> bool:
    if provider_code == "TOOL_SERVER_NOT_CONFIGURED":
        return False
    if status_code in {400, 401, 405, 422}:
        return False
    return provider_code == "TOOL_EXECUTION_FAILED" or status_code in {408, 429, 500, 502, 503, 504}
