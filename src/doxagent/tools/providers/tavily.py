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
            usable_results, rejected_results = _quality_filter_extract_results(results)
            if not usable_results:
                return self._failure(
                    request,
                    code="empty_or_low_quality_result",
                    message="Tavily extracted no substantive page content.",
                    details={
                        "urls": urls,
                        "failed_results": failed_results,
                        "quality_rejected": rejected_results,
                    },
                )
            output = {
                "provider": "tavily",
                # Keep each URL as an independent record so the Observation
                # kernel emits one or more independently citable O# blocks.
                "results": usable_results,
                "failed_results": failed_results or [],
                "quality_rejected": rejected_results,
            }
            if (isinstance(failed_results, list) and failed_results) or rejected_results:
                return self._partial(
                    request,
                    output=output,
                    raw=output,
                    source_kind="external_report",
                    source_id=f"tavily:extract:{len(urls)}",
                    title="Tavily URL extraction results",
                    summary="Tavily extracted some URLs while other URLs failed.",
                    source_scope="tavily_extract",
                    confidence=0.52,
                    metadata={"urls": urls, "format": body["format"]},
                    code="tavily_partial_extract",
                    message="Some requested URLs could not be extracted.",
                    details={
                        "failed_results": failed_results or [],
                        "quality_rejected": rejected_results,
                    },
                )
            return self._success(
                request,
                output=output,
                raw=output,
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


def _quality_filter_extract_results(
    results: list[object],
) -> tuple[list[JsonObject], list[JsonObject]]:
    usable: list[JsonObject] = []
    rejected: list[JsonObject] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        content = str(item.get("raw_content") or item.get("content") or "").strip()
        title = str(item.get("title") or "").strip()
        content = _trim_navigation_preamble(content, title)
        lowered = content[:3_000].lower()
        reason = ""
        if not content or len(content) < 200:
            reason = "content_too_short"
        elif any(
            marker in f"{title} {lowered}"
            for marker in (
                "page not found",
                "404 not found",
                "the page you requested could not be found",
                "access denied",
                "enable javascript and cookies to continue",
            )
        ):
            reason = "error_or_interstitial_page"
        else:
            navigation_hits = sum(
                lowered.count(marker)
                for marker in ("cookie policy", "privacy policy", "sign in", "menu", "chevron_")
            )
            if navigation_hits >= 15 and len(content) < 4_000:
                reason = "navigation_dominated"
        if reason:
            rejected.append({"url": url, "reason": reason, "title": title or None})
            continue
        usable.append(
            {
                "url": url,
                "title": title or None,
                "content": content[:200_000],
                "content_chars": len(content),
                "truncated": len(content) > 200_000,
            }
        )
    return usable, rejected


def _trim_navigation_preamble(content: str, title: str) -> str:
    if not content:
        return content
    start = -1
    if title:
        title_tail = title.split("::", maxsplit=1)[-1].strip()
        for prefix in (f"# {title}", f"# {title_tail}", title, title_tail):
            start = content.find(prefix)
            if start >= 0:
                break
    if start > 0:
        content = content[start:]
    # Federal Register and similar official pages often repeat a large
    # navigation/TOC envelope before the actual AGENCY/ACTION/SUMMARY text.
    # Preserve the document heading, then jump to the second semantic marker.
    toc = content.lower().find("table of contents")
    agency_positions: list[int] = []
    cursor = 0
    while True:
        found = content.find("AGENCY:", cursor)
        if found < 0:
            break
        agency_positions.append(found)
        cursor = found + len("AGENCY:")
        if len(agency_positions) >= 3:
            break
    if toc >= 0 and len(agency_positions) >= 2 and agency_positions[1] > toc:
        heading = f"# {title}" if title else "# Document"
        content = f"{heading}\n\n{content[agency_positions[1]:]}"
    return content.strip()


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    bounded = max(minimum, min(maximum, parsed))
    return int(bounded)
