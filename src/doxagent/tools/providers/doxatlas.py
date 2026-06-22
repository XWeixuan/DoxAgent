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
)
from doxagent.tools.schema import ToolRequest, ToolResult

_EVENT_SCOPE_FIELDS = frozenset(
    {"run_id", "narrative_code", "event_code", "narrative_id", "narrative_event_id"}
)
_RUN_NARRATIVE_EVENT_SCOPE_FIELDS = frozenset(
    {"run_id", "narrative_code", "event_code", "narrative_id", "narrative_event_id"}
)
_CONTENT_MODES = frozenset({"preview", "full", "none"})


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
    scope_kind: str | None = None
    cacheable: bool = True
    numeric_ranges: dict[str, tuple[int, int]] | None = None
    default_fields: dict[str, Any] | None = None
    content_mode_field: str | None = None


DOXATLAS_TOOL_SPECS: dict[str, EndpointSpec] = {
    "doxa_run_narrative_research": EndpointSpec(
        "run-narrative-research",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_run",
        "DoxAtlas Narrative Research 运行",
        "已请求 DoxAtlas Narrative Research 运行。",
        frozenset({"ticker", "language", "force"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        cacheable=False,
    ),
    "doxa_run_analysis": EndpointSpec(
        "run-analysis",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_run",
        "DoxAtlas 单标的分析任务",
        "已请求 DoxAtlas 单标的分析任务。",
        frozenset({"ticker", "language", "reuse_recent"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        cacheable=False,
    ),
    "doxa_get_narrative_report": EndpointSpec(
        "get-narrative-report",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_narrative_report",
        "DoxAtlas 叙事报告",
        "已检索 DoxAtlas 叙事报告。",
        frozenset({"ticker", "run_id", "view", "include_reasoning", "include_source_propositions"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        default_fields={"view": "agent_provenance"},
    ),
    "doxa_query_analysis": EndpointSpec(
        "query-analysis",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_analysis_tasks",
        "DoxAtlas analysis task 列表",
        "已检索 DoxAtlas analysis task 短代码列表。",
        frozenset({"ticker", "limit"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        numeric_ranges={"limit": (1, 50)},
    ),
    "doxa_get_analysis": EndpointSpec(
        "get-analysis",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_analysis",
        "DoxAtlas 单标的分析",
        "已检索 DoxAtlas 单标的分析。",
        frozenset({"ticker", "task_code", "task_id", "capsule_limit"}),
        required_fields=frozenset({"ticker"}),
        requires_ticker=True,
        numeric_ranges={"capsule_limit": (1, 20)},
    ),
    "doxa_query_propositions": EndpointSpec(
        "query-propositions",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_propositions",
        "DoxAtlas propositions",
        "已检索 DoxAtlas propositions。",
        _EVENT_SCOPE_FIELDS | frozenset({"proposition_id", "proposition_codes", "limit"}),
        scope_kind="event_or_proposition",
        numeric_ranges={"limit": (1, 50)},
    ),
    "doxa_get_ignored_propositions": EndpointSpec(
        "get-ignored-propositions",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_ignored_propositions",
        "DoxAtlas ignored propositions",
        "已检索 DoxAtlas ignored propositions。",
        _RUN_NARRATIVE_EVENT_SCOPE_FIELDS,
        scope_kind="run_narrative_event",
    ),
    "doxa_get_social_result": EndpointSpec(
        "get-social-result",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_social_result",
        "DoxAtlas social result",
        "已检索 DoxAtlas social result。",
        _EVENT_SCOPE_FIELDS | frozenset({"proposition_codes", "limit"}),
        scope_kind="event",
        numeric_ranges={"limit": (1, 50)},
    ),
    "doxa_get_social_result_detail": EndpointSpec(
        "get-social-result-detail",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_social_result_detail",
        "DoxAtlas social result detail",
        "已检索 DoxAtlas social result detail。",
        _EVENT_SCOPE_FIELDS | frozenset({"social_codes", "content_mode", "preview_chars"}),
        scope_kind="event",
        numeric_ranges={"preview_chars": (100, 8000)},
        content_mode_field="content_mode",
    ),
    "doxa_get_media_result": EndpointSpec(
        "get-media-result",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_media_result",
        "DoxAtlas media result",
        "已检索 DoxAtlas media result。",
        _EVENT_SCOPE_FIELDS | frozenset({"proposition_codes", "limit"}),
        scope_kind="event",
        numeric_ranges={"limit": (1, 50)},
    ),
    "doxa_get_media_result_detail": EndpointSpec(
        "get-media-result-detail",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_media_result_detail",
        "DoxAtlas media result detail",
        "已检索 DoxAtlas media result detail。",
        _EVENT_SCOPE_FIELDS | frozenset({"media_codes", "content_mode", "preview_chars"}),
        scope_kind="event",
        numeric_ranges={"preview_chars": (100, 8000)},
        content_mode_field="content_mode",
    ),
    "doxa_get_event_source": EndpointSpec(
        "get-event-source",
        EvidenceSourceType.DOXATLAS_SOURCE,
        "doxatlas_event_source",
        "DoxAtlas event source",
        "已检索 DoxAtlas event source。",
        _EVENT_SCOPE_FIELDS | frozenset({"source_codes", "limit", "content_mode", "preview_chars"}),
        scope_kind="event",
        numeric_ranges={"limit": (1, 20), "preview_chars": (100, 8000)},
        content_mode_field="content_mode",
    ),
}

DOXATLAS_ALIASES = {
    "doxatlas.query": "doxa_get_narrative_report",
    "doxatlas.source_lookup": "doxa_get_event_source",
}


def _validate_doxatlas_scope(payload: JsonObject, scope_kind: str) -> None:
    if _looks_like_doxagent_event_id(payload.get("narrative_event_id")):
        raise ValueError(
            "narrative_event_id must be a DoxAtlas event UUID, not a DoxAgent internal event_id."
        )
    if _looks_like_doxagent_event_id(payload.get("event_code")):
        raise ValueError("event_code must be a DoxAtlas short code such as E01.")

    if scope_kind == "event":
        if not _has_event_scope(payload):
            raise ValueError(
                "DoxAtlas event scope requires narrative_event_id, narrative_id+event_code, "
                "or run_id+narrative_code+event_code."
            )
        return
    if scope_kind == "event_or_proposition":
        has_proposition_id = bool(payload.get("proposition_id"))
        has_event_scope = _has_event_scope(payload)
        if has_proposition_id and (
            has_event_scope or payload.get("proposition_codes") is not None
        ):
            raise ValueError("proposition_id cannot be combined with event scope or proposition_codes.")
        if has_proposition_id or has_event_scope:
            return
        if payload.get("narrative_id") and not payload.get("event_code"):
            raise ValueError("doxa_query_propositions no longer accepts bare narrative_id.")
        raise ValueError(
            "doxa_query_propositions requires event scope or a single proposition_id."
        )
    if scope_kind == "run_narrative_event":
        if _has_event_scope(payload):
            return
        if payload.get("run_id") and not payload.get("event_code"):
            if payload.get("narrative_code") or not payload.get("event_code"):
                return
        if payload.get("narrative_id") and not payload.get("event_code"):
            return
        raise ValueError(
            "DoxAtlas ignored-proposition scope requires run_id, run_id+narrative_code, "
            "run_id+narrative_code+event_code, narrative_id, or narrative_event_id."
        )
    raise ValueError(f"Unsupported DoxAtlas scope kind: {scope_kind}.")


def _has_event_scope(payload: JsonObject) -> bool:
    if payload.get("narrative_event_id"):
        return True
    if payload.get("narrative_id") and payload.get("event_code"):
        return True
    return bool(payload.get("run_id") and payload.get("narrative_code") and payload.get("event_code"))


def _looks_like_doxagent_event_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("event_")


def _validate_content_mode(value: object) -> None:
    if value not in _CONTENT_MODES:
        allowed = ", ".join(sorted(_CONTENT_MODES))
        raise ValueError(f"content_mode must be one of: {allowed}.")


def _validate_doxatlas_code_arrays(payload: JsonObject, endpoint: str) -> None:
    list_fields = ("proposition_codes", "media_codes", "social_codes", "source_codes")
    for field in list_fields:
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError(f"{field} must be a list of non-empty short-code strings.")
    if endpoint == "get-media-result-detail" and not payload.get("media_codes"):
        raise ValueError("media_codes is required for doxa_get_media_result_detail.")
    if endpoint == "get-social-result-detail" and not payload.get("social_codes"):
        raise ValueError("social_codes is required for doxa_get_social_result_detail.")


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
        for field, value in (spec.default_fields or {}).items():
            payload.setdefault(field, value)
        if spec.requires_ticker and "ticker" not in payload:
            payload["ticker"] = request.ticker
        if spec.single_scope_id:
            raise ValueError("single_scope_id is no longer supported for DoxAtlas scoped tools.")
        if spec.scope_kind:
            _validate_doxatlas_scope(payload, spec.scope_kind)
        if spec.content_mode_field and spec.content_mode_field in payload:
            _validate_content_mode(payload[spec.content_mode_field])

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
        _validate_doxatlas_code_arrays(payload, spec.endpoint)
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
