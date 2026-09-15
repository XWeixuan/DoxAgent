"""Yahoo Finance, Google News and Reuters Message Bus source adapters."""

from __future__ import annotations

import json
import re
import time as monotonic_time
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Protocol, cast
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from cdecr.kb_v2 import V2KnowledgeBase
from doxagent.message_bus_v2.google_news import resolve_google_news_urls
from doxagent.message_bus_v2.schema import (
    AcquisitionFailure,
    JsonObject,
    PollContext,
    PollResult,
    RawMessageInput,
    canonical_json,
    sha256_text,
)
from doxagent.settings import DoxAgentSettings


class ReutersBrowser(Protocol):
    async def reuters_search(self, query: str, offset: int) -> list[dict[str, object]]: ...


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, UTC)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
            return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            return None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean(value: object) -> str | None:
    if value is None:
        return None
    parser = _Text()
    parser.feed(str(value))
    parser.close()
    return " ".join(unescape(" ".join(parser.parts)).split()) or None


def _failure(
    context: PollContext, code: str, message: str, payload: JsonObject
) -> AcquisitionFailure:
    return AcquisitionFailure(
        source_id=context.source.source_id,
        binding_id=context.binding.binding_id,
        ticker=context.ticker,
        error_code=code,
        error_message=message,
        raw_hash=sha256_text(canonical_json(payload)),
        original_payload=payload,
    )


def _domain(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").casefold().removeprefix("www.")


def _company_search_name(value: str) -> str:
    result = value.strip()
    suffixes = (
        r",?\s+(?:incorporated|inc|corporation|corp|company|co|limited|ltd|plc|holdings)\.?$",
        r"\s+(?:technology|technologies)\.?$",
    )
    changed = True
    while changed:
        changed = False
        for pattern in suffixes:
            shortened = re.sub(pattern, "", result, flags=re.IGNORECASE).strip(" ,.")
            if shortened != result:
                result, changed = shortened, True
    return result or value.strip()


def _yahoo_contents(value: object) -> list[JsonObject]:
    found: list[JsonObject] = []
    if isinstance(value, list):
        for child in value:
            found.extend(_yahoo_contents(child))
    elif isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, dict) and content.get("title") and content.get("pubDate"):
            found.append(dict(cast(dict[str, Any], content)))
        elif value.get("title") and (value.get("pubDate") or value.get("providerPublishTime")):
            found.append(dict(cast(dict[str, Any], value)))
        else:
            for child in value.values():
                if isinstance(child, (dict, list)):
                    found.extend(_yahoo_contents(child))
    unique: dict[str, JsonObject] = {}
    for row in found:
        key = str(row.get("id") or row.get("uuid") or row.get("link") or canonical_json(row))
        unique[key] = row
    return list(unique.values())


def _reader_proxy_json(value: str) -> JsonObject:
    """Extract the upstream JSON body from a Jina Reader text response."""

    marker = "Markdown Content:\n"
    content = value.split(marker, 1)[1] if marker in value else value
    start = content.find("{")
    if start < 0:
        raise ValueError("Yahoo reader proxy response did not contain JSON")
    parsed, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(parsed, dict):
        raise ValueError("Yahoo reader proxy response was not a JSON object")
    return dict(cast(dict[str, Any], parsed))


class YahooFinanceNewsAdapter:
    def __init__(
        self, settings: DoxAgentSettings, client: httpx.AsyncClient, *, transport=None, browser=None
    ) -> None:
        from .yahoo_transport import shared_yahoo_transport

        self.settings = settings
        # The registry's httpx client remains for other providers; Yahoo owns no session.
        self.transport = transport or shared_yahoo_transport()
        self.browser = browser
        self._browser_retry_at = 0.0
        self._page_api_retry_at = 0.0
        self._page_strikes = 0

    async def poll(self, context: PollContext) -> PollResult:
        from .yahoo_sources import acquire_yahoo_rss
        from .yahoo_transport import YahooRateLimited

        count = (
            100
            if context.is_bootstrap or context.is_gap_recovery
            else max(10, min(20, int(context.binding.source_parameters.get("snippet_count", 20))))
        )
        attempts = []
        if (
            context.binding.source_parameters.get("page_network_enabled", False)
            and self.browser is not None
            and monotonic_time.monotonic() >= self._browser_retry_at
        ):
            # Reserve the probe before awaiting: concurrent tickers must not queue
            # repeated browser probes while the first one is still running.
            self._browser_retry_at = monotonic_time.monotonic() + 300
            try:
                async with context.request_permit():
                    rows, metadata = await self.browser.yahoo_latest_news(
                        context.ticker,
                        snippet_count=count,
                    )
                self._browser_retry_at = 0.0
                self._page_strikes = 0
                return self._map_result(
                    context, rows, "page_network_ncp", count, {**metadata, "attempts": attempts}
                )
            except Exception as exc:
                self._browser_retry_at = monotonic_time.monotonic() + 300
                if getattr(exc, "status_code", None) == 429:
                    self._page_strikes += 1
                    delay = max(
                        (300, 900, 1800)[min(self._page_strikes - 1, 2)],
                        getattr(exc, "retry_after_seconds", 0),
                    )
                    self._browser_retry_at = monotonic_time.monotonic() + delay
                    self._page_api_retry_at = self._browser_retry_at
                attempts.append({"route": "page_network_ncp", "error": type(exc).__name__})
        try:
            if monotonic_time.monotonic() < self._page_api_retry_at:
                raise YahooRateLimited(self._page_api_retry_at - monotonic_time.monotonic())
            async with context.request_permit():
                response = await self.transport.request(
                    "POST",
                    "https://finance.yahoo.com/xhr/ncp",
                    params={"queryRef": "latestNews", "serviceKey": "ncp_fin"},
                    json={"serviceConfig": {"snippetCount": count, "s": [context.ticker]}},
                    timeout=self.settings.tool_http_timeout_seconds,
                )
            payload = response.json()
            rows = _yahoo_contents(payload)
            if (
                not rows
                and not (
                    isinstance(payload, dict)
                    and any(k in payload for k in ("data", "finance", "news"))
                )
                and payload != []
            ):
                raise ValueError("Yahoo NCP response schema unrecognized")
            return self._map_result(context, rows, "ncp_latest_news", count, {"attempts": attempts})
        except (httpx.HTTPError, ValueError, YahooRateLimited) as exc:
            attempts.append({"route": "ncp_latest_news", "error": type(exc).__name__})
        # RSS is a separate endpoint family, not another Query API retry.
        # Its success must not clear the NCP/API rate-limit circuit.
        async with context.request_permit():
            rows, metadata = await acquire_yahoo_rss(
                self.transport,
                context.ticker,
                timeout_seconds=self.settings.tool_http_timeout_seconds,
            )
        return self._map_result(
            context, rows, "legacy_headline_rss", count, {**metadata, "attempts": attempts}
        )

    async def poll_legacy_search(self, context: PollContext) -> PollResult:
        """Retained for explicit diagnostics only; never used by automatic polling."""
        from .yahoo_transport import YahooEndpointUnavailable

        count = (
            100
            if context.is_bootstrap or context.is_gap_recovery
            else max(10, min(20, int(context.binding.source_parameters.get("snippet_count", 20))))
        )
        mode = "ncp_latest_news"
        try:
            async with context.request_permit():
                response = await self.transport.request(
                    "POST",
                    "https://finance.yahoo.com/xhr/ncp",
                    params={"queryRef": "latestNews", "serviceKey": "ncp_fin"},
                    json={"serviceConfig": {"snippetCount": count, "s": [context.ticker]}},
                    timeout=self.settings.tool_http_timeout_seconds,
                )
                payload = response.json()
                rows = _yahoo_contents(payload)
                if (
                    not rows
                    and not (
                        isinstance(payload, dict)
                        and any(key in payload for key in ("data", "finance", "news"))
                    )
                    and payload != []
                ):
                    raise ValueError("Yahoo NCP response schema unrecognized")
        except (YahooEndpointUnavailable, ValueError):
            rows = []
            mode = "finance_search_fallback"
            fallback_count = min(count, 10)
            last_error: Exception | None = None
            for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
                try:
                    async with context.request_permit():
                        response = await self.transport.request(
                            "GET",
                            f"https://{host}/v1/finance/search",
                            params={
                                "q": context.ticker,
                                "newsCount": fallback_count,
                                "quotesCount": 0,
                            },
                            timeout=self.settings.tool_http_timeout_seconds,
                        )
                        rows = _yahoo_contents(response.json().get("news", []))
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
            if not rows and last_error is not None:
                proxy_url = self.settings.message_bus_v2_yahoo_reader_proxy_url.strip()
                if not proxy_url:
                    raise last_error from None
                mode = "finance_search_reader_proxy_fallback"
                try:
                    async with context.request_permit():
                        response = await self.transport.request(
                            "GET",
                            proxy_url,
                            params={
                                "q": context.ticker,
                                "newsCount": fallback_count,
                                "quotesCount": 0,
                            },
                            timeout=self.settings.tool_http_timeout_seconds,
                        )
                        payload = _reader_proxy_json(response.text)
                        rows = _yahoo_contents(payload.get("news", []))
                except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                    raise exc from None

        return self._map_result(
            context, rows, mode, count if mode == "ncp_latest_news" else fallback_count
        )

    def _map_result(self, context, rows, mode, count, metadata=None) -> PollResult:
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        for row in rows:
            original_time = row.get("pubDate") or row.get("providerPublishTime")
            published = _dt(original_time)
            basis = "EXACT"
            if isinstance(original_time, str) and len(original_time.strip()) == 10:
                published = datetime.fromisoformat(original_time).replace(
                    hour=12, tzinfo=ZoneInfo("America/New_York")
                )
                basis = "DATE"
            if published is None:
                published, basis = context.requested_at, "UNKNOWN_FIRST_SEEN"
            if basis == "EXACT" and (
                published < (context.window_start or context.requested_at - timedelta(hours=24))
                or published
                > (context.window_cutoff or context.requested_at) + timedelta(minutes=5)
            ):
                continue
            provider = row.get("provider") if isinstance(row.get("provider"), dict) else {}
            canonical = row.get("canonicalUrl") if isinstance(row.get("canonicalUrl"), dict) else {}
            click = (
                row.get("clickThroughUrl") if isinstance(row.get("clickThroughUrl"), dict) else {}
            )
            url = str(
                cast(dict[str, Any], canonical).get("url")
                or cast(dict[str, Any], click).get("url")
                or row.get("link")
                or ""
            )
            if not url.startswith(("http://", "https://")):
                failures.append(
                    _failure(context, "missing_absolute_url", "Yahoo item has no URL", row)
                )
                continue
            publisher = str(
                cast(dict[str, Any], provider).get("displayName")
                or row.get("publisher")
                or "Yahoo Finance"
            )
            provider_url = str(cast(dict[str, Any], provider).get("url") or url)
            identifier = str(row.get("id") or row.get("uuid") or sha256_text(url))
            messages.append(
                RawMessageInput(
                    external_id=identifier,
                    source_item_key=identifier,
                    title=_clean(row.get("title")),
                    body=None,
                    summary=_clean(row.get("summary") or row.get("description")),
                    source=publisher,
                    publisher_name=publisher,
                    url=url,
                    published_at=published,
                    publication_time_basis=basis,
                    raw_payload=row,
                    metadata={
                        "provider": "yahoo_finance",
                        "identity_evidence": {
                            "id_kind": "stable_article",
                            "url_kind": "article",
                            "mutable": True,
                        },
                        "query_mode": mode,
                        **(
                            {"capture_method": metadata["capture_method"]}
                            if metadata and "capture_method" in metadata
                            else {}
                        ),
                        "publisher_domain": _domain(provider_url),
                    },
                )
            )
        return PollResult(
            messages=messages,
            failures=failures,
            window_coverage="COMPLETE" if mode == "ncp_latest_news" and not failures else "PARTIAL",
            acquisition_metadata={
                "provider": "yahoo_finance",
                "query_mode": mode,
                "window_hours": 24,
                "requested_count": count
                if mode not in ("page_network_ncp", "legacy_headline_rss")
                else None,
                "endpoint": (
                    "query1_via_reader_proxy"
                    if mode == "finance_search_reader_proxy_fallback"
                    else mode
                ),
                **(metadata or {}),
            },
        )


def _rss_rows(xml_text: str) -> list[JsonObject]:
    root = ElementTree.fromstring(xml_text)
    rows: list[JsonObject] = []
    for item in root.findall(".//item"):
        row: JsonObject = {}
        for child in item:
            key = child.tag.rsplit("}", 1)[-1]
            text_value = (child.text or "").strip()
            if text_value:
                row[key] = text_value
            if key == "source" and child.attrib.get("url"):
                row["source_url"] = child.attrib["url"]
        rows.append(row)
    return rows


class GoogleNewsSearchRssAdapter:
    def __init__(self, settings: DoxAgentSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self._resolved_urls: dict[str, str] = {}

    async def poll(self, context: PollContext) -> PollResult:
        terms = [str(item).strip() for item in context.binding.source_parameters["search_terms"]]
        domains = [
            str(item).strip().casefold().removeprefix("www.")
            for item in context.binding.source_parameters.get("domains", [])
        ]
        query = " OR ".join(f'"{term}"' if " " in term else term for term in terms)
        if len(terms) > 1:
            query = f"({query})"
        if domains:
            query += " (" + " OR ".join(f"site:{item}" for item in domains) + ")"
        query += " when:1d"
        url = "https://news.google.com/rss/search?" + urlencode(
            {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        )
        async with context.request_permit():
            response = await self.client.get(
                url, headers={"User-Agent": self.settings.monitoring_rss_user_agent}
            )
            response.raise_for_status()
        rows = _rss_rows(response.text)
        wrappers = [str(row.get("link")) for row in rows if row.get("link")]
        unresolved = [item for item in wrappers if item not in self._resolved_urls]
        try:
            resolved = await resolve_google_news_urls(
                self.client, unresolved, context.request_permit
            )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            resolved = {}
        self._resolved_urls.update(resolved)
        while len(self._resolved_urls) > 5_000:
            self._resolved_urls.pop(next(iter(self._resolved_urls)))
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        for row in rows:
            original_time = row.get("pubDate")
            published = _dt(original_time)
            basis = "EXACT"
            if isinstance(original_time, str) and len(original_time.strip()) == 10:
                published = datetime.fromisoformat(original_time).replace(
                    hour=12, tzinfo=ZoneInfo("America/New_York")
                )
                basis = "DATE"
            if published is None:
                published, basis = context.requested_at, "UNKNOWN_FIRST_SEEN"
            if basis == "EXACT" and (
                published < (context.window_start or context.requested_at - timedelta(hours=24))
                or published
                > (context.window_cutoff or context.requested_at) + timedelta(minutes=5)
            ):
                continue
            wrapper = str(row.get("link") or "")
            target = self._resolved_urls.get(wrapper, wrapper)
            if not target.startswith(("http://", "https://")):
                failures.append(
                    _failure(
                        context,
                        "missing_absolute_url",
                        "Google News item has no absolute URL",
                        row,
                    )
                )
                continue
            publisher = str(row.get("source") or "Google News")
            publisher_domain = _domain(str(row.get("source_url") or target))
            if domains and not any(
                publisher_domain == item or publisher_domain.endswith(f".{item}")
                for item in domains
            ):
                failures.append(
                    _failure(
                        context,
                        "domain_constraint_unresolved",
                        "Google item did not resolve to an allowed domain",
                        row,
                    )
                )
                continue
            identifier = str(row.get("guid") or sha256_text(wrapper))
            messages.append(
                RawMessageInput(
                    external_id=identifier,
                    source_item_key=identifier,
                    title=_clean(row.get("title")),
                    body=None,
                    summary=_clean(row.get("description")),
                    source=publisher,
                    publisher_name=publisher,
                    url=target,
                    published_at=published,
                    publication_time_basis=basis,
                    raw_payload=row,
                    metadata={
                        "provider": "google_news",
                        "query": query,
                        "google_wrapper_url": wrapper,
                        "publisher_domain": publisher_domain,
                        "canonical_resolution": (
                            "resolved" if wrapper in self._resolved_urls else "unresolved"
                        ),
                    },
                )
            )
        return PollResult(
            messages=messages,
            failures=failures,
            window_coverage="PARTIAL" if failures else "COMPLETE",
            acquisition_metadata={"provider": "google_news", "query": query, "window_hours": 24},
        )


def _reuters_date(value: object) -> date | None:
    text_value = str(value or "").strip()
    for pattern in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text_value, pattern).date()
        except ValueError:
            pass
    parsed = _dt(value)
    return parsed.date() if parsed else None


class ReutersSiteSearchAdapter:
    def __init__(
        self,
        browser: ReutersBrowser | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.browser = browser
        self.client = client
        self._company_names: dict[str, str] = {}
        self._company_kb = V2KnowledgeBase()

    async def _query(self, context: PollContext) -> tuple[str, str]:
        configured = context.binding.source_parameters.get("company_short_name")
        if configured:
            return str(configured).strip(), "binding"
        if context.ticker in self._company_names:
            return self._company_names[context.ticker], "company_name_cache"
        catalog_matches = self._company_kb.lookup("companies", context.ticker, limit=8)
        expected_id = "COMPANY_" + re.sub(r"[^A-Z0-9]+", "_", context.ticker.upper()).strip("_")
        catalog_match = next(
            (item for item in catalog_matches if item.external_id == expected_id),
            catalog_matches[0] if len(catalog_matches) == 1 else None,
        )
        if catalog_match is not None:
            ticker = context.ticker.casefold()
            names = [catalog_match.name, *catalog_match.aliases]
            candidates = [
                _company_search_name(name) for name in names if name.strip().casefold() != ticker
            ]
            if not candidates:
                candidates = [catalog_match.name]
            search_name = min(candidates, key=lambda value: (len(value), value.casefold()))
            self._company_names[context.ticker] = search_name
            return search_name, "cdecr_company_catalog"
        if self.client is not None:
            try:
                async with context.request_permit():
                    response = await self.client.get(
                        "https://query1.finance.yahoo.com/v1/finance/search",
                        params={"q": context.ticker, "quotesCount": 1, "newsCount": 0},
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    response.raise_for_status()
                quotes = response.json().get("quotes", [])
                if quotes and isinstance(quotes[0], dict):
                    name = str(
                        quotes[0].get("shortname") or quotes[0].get("longname") or ""
                    ).strip()
                    if name:
                        search_name = _company_search_name(name)
                        self._company_names[context.ticker] = search_name
                        return search_name, "yahoo_symbol_lookup"
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
        return context.ticker, "ticker_fallback"

    async def poll(self, context: PollContext) -> PollResult:
        if self.browser is None:
            raise RuntimeError("Reuters source requires the shared Playwright browser runtime")
        query, query_source = await self._query(context)
        max_pages = int(context.binding.source_parameters.get("max_pages", 2))
        cutoff = (context.window_cutoff or context.requested_at).astimezone(
            ZoneInfo("America/New_York")
        ).date() - timedelta(days=1)
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        seen: set[str] = set()
        stopped_on_old = False
        exhausted = False
        search_result_count = 0
        pages_fetched = 0
        for page in range(max_pages):
            async with context.request_permit():
                rows = await self.browser.reuters_search(query, page * 20)
            pages_fetched += 1
            search_result_count += len(rows)
            if not rows:
                exhausted = True
                break
            for raw in rows:
                row = dict(raw)
                published_date = _reuters_date(row.get("date"))
                basis = "DATE" if published_date is not None else "UNKNOWN_FIRST_SEEN"
                if published_date is not None and published_date < cutoff:
                    stopped_on_old = True
                    continue
                url = str(row.get("url") or "")
                if url.startswith("/"):
                    url = "https://www.reuters.com" + url
                if not url.startswith(("http://", "https://")) or url in seen:
                    if not url.startswith(("http://", "https://")):
                        failures.append(
                            _failure(
                                context,
                                "missing_absolute_url",
                                "Reuters result has no absolute article URL",
                                cast(JsonObject, row),
                            )
                        )
                    continue
                seen.add(url)
                identifier = sha256_text(url)
                published = (
                    datetime.combine(
                        published_date, time(hour=12), tzinfo=ZoneInfo("America/New_York")
                    )
                    if published_date
                    else context.requested_at
                )
                relative = re.fullmatch(
                    r"(\d+)\s+(mins?|minutes?|hours?)\s+ago",
                    str(row.get("relative_time") or ""),
                    re.I,
                )
                estimated_time = None
                if relative:
                    delta = (
                        timedelta(hours=int(relative[1]))
                        if relative[2].lower().startswith("hour")
                        else timedelta(minutes=int(relative[1]))
                    )
                    estimated_time = (context.requested_at - delta).isoformat()
                card_date = _reuters_date(row.get("card_date"))
                messages.append(
                    RawMessageInput(
                        external_id=identifier,
                        source_item_key=identifier,
                        title=_clean(row.get("title")),
                        body=None,
                        summary=_clean(row.get("summary")),
                        source="Reuters",
                        publisher_name="Reuters",
                        url=url,
                        published_at=published,
                        publication_time_basis=basis,
                        raw_payload=cast(JsonObject, row),
                        metadata={
                            "provider": "reuters",
                            "identity_evidence": {
                                "id_kind": "article_url",
                                "url_kind": "article",
                                "mutable": True,
                            },
                            "date_basis": row.get("date_basis", "url_date"),
                            "card_date": row.get("card_date"),
                            "relative_time": row.get("relative_time"),
                            "estimated_publication_time": estimated_time,
                            "date_conflict": bool(
                                card_date and published_date and card_date != published_date
                            ),
                            "query": query,
                            "query_source": query_source,
                            "publication_time_precision": "day" if published_date else "unknown",
                            "search_result_date": published_date.isoformat()
                            if published_date
                            else None,
                        },
                    )
                )
            if stopped_on_old or len(rows) < 20:
                exhausted = True
                break
        return PollResult(
            messages=messages,
            failures=failures,
            window_coverage=(
                "PARTIAL" if failures else "COMPLETE" if stopped_on_old or exhausted else "UNKNOWN"
            ),
            acquisition_metadata={
                "provider": "reuters",
                "query": query,
                "query_source": query_source,
                "date_cutoff": cutoff.isoformat(),
                "date_window": "today_and_previous_day",
                "pages_fetched": pages_fetched,
                "search_result_count": search_result_count,
            },
        )


__all__ = ["GoogleNewsSearchRssAdapter", "ReutersSiteSearchAdapter", "YahooFinanceNewsAdapter"]
