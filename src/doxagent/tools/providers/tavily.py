"""Tavily provider tools."""

from __future__ import annotations

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import BaseRealToolClient, _input_list, _input_str, _require
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
            return self._success(
                request,
                output={"provider": "tavily", "search": raw},
                raw=raw,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"tavily:search:{body['query']}",
                title="Tavily search",
                summary="Tavily search results were retrieved.",
                citation_scope="tavily_search",
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
                raise ValueError("urls is required.")
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
            return self._success(
                request,
                output={"provider": "tavily", "extract": raw},
                raw=raw,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"tavily:extract:{len(urls)}",
                title="Tavily extract",
                summary="Tavily URL extraction results were retrieved.",
                citation_scope="tavily_extract",
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


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    bounded = max(minimum, min(maximum, parsed))
    return int(bounded)
