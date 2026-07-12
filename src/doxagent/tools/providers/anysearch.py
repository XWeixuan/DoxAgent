"""AnySearch provider tools."""

from __future__ import annotations

from collections.abc import Mapping

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
)
from doxagent.tools.schema import ToolRequest, ToolResult

ANYSEARCH_ZONES = {"cn", "intl"}
ANYSEARCH_DOMAINS = {
    "academic",
    "business",
    "code",
    "ecommerce",
    "education",
    "energy",
    "environment",
    "fashion",
    "film",
    "finance",
    "gaming",
    "general",
    "geo",
    "health",
    "home",
    "ip",
    "legal",
    "music",
    "religion",
    "security",
    "tech",
    "travel",
}


class AnySearchSearchClient(BaseRealToolClient):
    """Call AnySearch's unified search endpoint."""

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            query = _input_str(request, "query", "").strip()
            if not query:
                query = f"{request.ticker} public source verification"
            body = _search_body(request, query)
            headers = {"Content-Type": "application/json"}
            if self.settings.anysearch_api_key:
                headers["Authorization"] = f"Bearer {self.settings.anysearch_api_key}"
            raw = self._post_json(
                self.settings.anysearch_base_url.rstrip("/") + "/v1/search",
                json_body=body,
                headers=headers,
                cache_ttl=self.settings.anysearch_cache_ttl_seconds,
            )
            _raise_for_anysearch_error(raw)
            data = raw.get("data")
            results = data.get("results") if isinstance(data, Mapping) else None
            if not isinstance(results, list) or not results:
                return self._failure(
                    request,
                    code="empty_result",
                    message="AnySearch returned no search results.",
                    details={"query": query},
                )
            metadata = _response_metadata(raw)
            return self._success(
                request,
                output={"provider": "anysearch", "search": raw},
                raw=raw,
                source_kind="external_report",
                source_id=f"anysearch:search:{query}",
                title="AnySearch 搜索结果",
                summary="已检索 AnySearch 搜索结果。",
                source_scope="anysearch_search",
                confidence=0.62,
                metadata={**body, **metadata},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _search_body(request: ToolRequest, query: str) -> JsonObject:
    body: JsonObject = {
        "query": query,
        "max_results": _bounded_int(request.input.get("max_results", 5), 1, 100),
    }
    domain = _optional_str(request.input.get("domain")).lower()
    if domain in ANYSEARCH_DOMAINS:
        body["domain"] = domain
    tag = _optional_str(request.input.get("tag"))
    if tag:
        body["tag"] = tag
    content_types = _input_list(request, "content_types")
    if content_types:
        body["content_types"] = content_types
    zone = _optional_str(request.input.get("zone")).lower()
    if zone in ANYSEARCH_ZONES:
        body["zone"] = zone
    language = _optional_str(request.input.get("language"))
    if language:
        body["language"] = language
    params = request.input.get("params")
    if isinstance(params, Mapping):
        body["params"] = dict(params)
    return body


def _raise_for_anysearch_error(raw: JsonObject) -> None:
    code = raw.get("code")
    if code in (None, 0, "0"):
        return
    raw_data = raw.get("data")
    data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
    raise ProviderHttpError(
        code="upstream_api_error",
        message=str(raw.get("message") or "AnySearch returned an error."),
        retryable=str(code).startswith(("429", "500", "502", "503", "504")),
        details={"provider_code": code, "provider_data": data},
    )


def _response_metadata(raw: JsonObject) -> JsonObject:
    data = raw.get("data")
    if not isinstance(data, Mapping):
        return {}
    metadata = data.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _optional_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))
