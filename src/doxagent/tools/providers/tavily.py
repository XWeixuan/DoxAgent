"""Tavily provider tools."""

from __future__ import annotations

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult

TAVILY_SEARCH_DEPTHS = {"ultra-fast", "fast", "basic", "advanced"}
TAVILY_SEARCH_DEPTH_ALIASES = {
    "ultrafast": "ultra-fast",
    "ultra_fast": "ultra-fast",
    "medium": "basic",
    "normal": "basic",
    "standard": "basic",
    "deep": "advanced",
}
TAVILY_TOPICS = {"general", "news", "finance"}


class TavilySearchClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.tavily_api_key, "TAVILY_API_KEY")
            search_depth = _normalize_search_depth(_input_str(request, "search_depth", "basic"))
            topic = _input_str(request, "topic", "finance").lower()
            if topic not in TAVILY_TOPICS:
                topic = "finance"
            body = {
                "query": _input_str(request, "query", f"{request.ticker} industry research"),
                "topic": topic,
                "search_depth": search_depth,
                "max_results": _bounded_int(request.input.get("max_results", 5), 1, 20),
            }
            raw = self._post_json(
                self.settings.tavily_base_url.rstrip("/") + "/search",
                json_body=body,
                headers={"Authorization": f"Bearer {api_key}"},
                cache_ttl=self.settings.tavily_cache_ttl_seconds,
            )
            _raise_tavily_error(raw)
            results = raw.get("results")
            if not isinstance(results, list) or not results:
                return self._failure(
                    request,
                    code="empty_result",
                    message="Tavily returned no search results.",
                    details={"query": body["query"]},
                )
            return self._success(
                request,
                output={"provider": "tavily", "search": raw},
                raw=raw,
                source_kind="external_report",
                source_id=f"tavily:search:{body['query']}",
                title="Tavily 搜索结果",
                summary="已检索 Tavily 搜索结果。",
                source_scope="tavily_search",
                confidence=0.6,
                metadata=body,
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class TavilyExtractClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.tavily_api_key, "TAVILY_API_KEY")
            urls = _input_list(request, "urls")
            if not urls:
                raise ValueError("urls 为必填项。")
            body = {
                "urls": urls,
                "extract_depth": _input_str(request, "extract_depth", "basic"),
                "format": _input_str(request, "format", "markdown"),
            }
            raw = self._post_json(
                self.settings.tavily_base_url.rstrip("/") + "/extract",
                json_body=body,
                headers={"Authorization": f"Bearer {api_key}"},
                cache_ttl=self.settings.tavily_cache_ttl_seconds,
            )
            _raise_tavily_error(raw)
            results = raw.get("results")
            failed_results = raw.get("failed_results")
            if not isinstance(results, list) or not results:
                return self._failure(
                    request,
                    code="empty_result",
                    message="Tavily extracted no URL content.",
                    details={"urls": urls, "failed_results": failed_results},
                )
            output = {"provider": "tavily", "extract": raw}
            if isinstance(failed_results, list) and failed_results:
                return self._partial(
                    request,
                    output=output,
                    raw=raw,
                    source_kind="external_report",
                    source_id=f"tavily:extract:{len(urls)}",
                    title="Tavily URL extraction results",
                    summary="Tavily extracted some URLs while other URLs failed.",
                    source_scope="tavily_extract",
                    confidence=0.52,
                    metadata={"urls": urls, "format": body["format"]},
                    code="tavily_partial_extract",
                    message="Some requested URLs could not be extracted.",
                    details={"failed_results": failed_results},
                )
            return self._success(
                request,
                output=output,
                raw=raw,
                source_kind="external_report",
                source_id=f"tavily:extract:{len(urls)}",
                title="Tavily URL 抽取结果",
                summary="已检索 Tavily URL 抽取结果。",
                source_scope="tavily_extract",
                confidence=0.62,
                metadata={"urls": urls, "format": body["format"]},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _normalize_search_depth(value: str) -> str:
    normalized = value.strip().lower()
    normalized = TAVILY_SEARCH_DEPTH_ALIASES.get(normalized, normalized)
    if normalized in TAVILY_SEARCH_DEPTHS:
        return normalized
    return "basic"


def _raise_tavily_error(raw: JsonObject) -> None:
    message = raw.get("error") or raw.get("detail")
    if message in (None, "", [], {}):
        return
    rendered = str(message)
    retryable = any(token in rendered.lower() for token in ("rate", "quota", "limit"))
    raise ProviderHttpError(
        code="rate_limited" if retryable else "upstream_provider_error",
        message=rendered,
        retryable=retryable,
        details={"provider_payload": raw},
    )


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    bounded = max(minimum, min(maximum, parsed))
    return int(bounded)
