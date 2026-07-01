"""External collectors for monitoring sources."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, cast
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from doxagent.monitoring.schema import (
    EndpointKind,
    FetchedExternalMessage,
    JsonObject,
    MonitoringSourceConfig,
    TickerSourceBinding,
)
from doxagent.settings import DoxAgentSettings


class CollectorError(RuntimeError):
    """Base monitoring collector error."""


class MissingCredentialError(CollectorError):
    """Raised when a source is enabled but its API credential is absent."""


class MonitoringCollector(Protocol):
    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        ...


class MonitoringCollectorRegistry:
    def __init__(self, settings: DoxAgentSettings, *, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.tool_http_timeout_seconds)

    def collector_for(self, source: MonitoringSourceConfig) -> MonitoringCollector:
        if source.endpoint_kind is EndpointKind.BENZINGA_NEWS:
            return BenzingaNewsCollector(self.settings, self.client)
        if source.endpoint_kind is EndpointKind.FINNHUB_COMPANY_NEWS:
            return FinnhubCompanyNewsCollector(self.settings, self.client)
        if source.endpoint_kind is EndpointKind.STOCKTWITS_MESSAGES:
            return StocktwitsMessagesCollector(self.settings, self.client)
        if source.endpoint_kind is EndpointKind.TIKHUB_X_SEARCH:
            return TikHubXSearchCollector(self.settings, self.client)
        if source.endpoint_kind is EndpointKind.TIKHUB_X_USER_POSTS:
            return TikHubXUserPostsCollector(self.settings, self.client)
        if source.endpoint_kind is EndpointKind.RSS_FEED:
            return RSSFeedCollector(self.settings, self.client)
        raise CollectorError(f"No collector registered for {source.endpoint_kind.value}.")


class BaseCollector:
    def __init__(self, settings: DoxAgentSettings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client

    def _get_json(
        self,
        url: str,
        *,
        params: dict[str, object | None] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> object:
        filtered_params = {
            key: value for key, value in (params or {}).items() if value is not None
        }
        kwargs: dict[str, Any] = {
            "params": cast(Any, filtered_params),
            "headers": headers,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = self.client.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def _get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        response = self.client.get(url, headers=headers)
        response.raise_for_status()
        return response.text

    def _fetched(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
        payload: JsonObject,
        provider_message_id: str | None,
        source_url: str | None,
        source_published_at: datetime | None,
        metadata: JsonObject | None = None,
    ) -> FetchedExternalMessage:
        return FetchedExternalMessage(
            source_id=source.source_id,
            binding_id=binding.binding_id,
            ticker=binding.ticker,
            source_type=source.source_type,
            interface_type=source.interface_type,
            raw_payload=payload,
            provider_message_id=provider_message_id,
            source_url=source_url,
            source_published_at=source_published_at,
            metadata=metadata or {},
        )


class BenzingaNewsCollector(BaseCollector):
    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        token = _require(self.settings.benzinga_api_key, "BENZINGA_API_KEY")
        page_size = int(source.config.get("page_size", 50))
        url = self.settings.benzinga_news_base_url.rstrip("/") + "/api/v2/news"
        base_params = {
            "token": token,
            "pageSize": min(page_size, 100),
            "displayOutput": source.config.get("display_output", "full"),
            "sort": source.config.get("sort", "created:desc"),
        }
        data = self._get_json(
            url,
            params={**base_params, "tickers": binding.ticker},
            headers={"accept": "application/json"},
        )
        rows = _object_list(data)
        if not rows and binding.parameters.search_terms:
            fallback_rows: list[JsonObject] = []
            for topic in binding.parameters.search_terms:
                topic_data = self._get_json(
                    url,
                    params={**base_params, "topics": topic},
                    headers={"accept": "application/json"},
                )
                fallback_rows.extend(
                    row
                    for row in _object_list(topic_data)
                    if _benzinga_row_matches(row, binding.ticker, topic)
                )
            rows = _dedupe_rows_by_provider_id(fallback_rows)
        return [
            self._fetched(
                source=source,
                binding=binding,
                payload=row,
                provider_message_id=_str_or_none(row.get("id")),
                source_url=_str_or_none(row.get("url")),
                source_published_at=_parse_datetime(row.get("created") or row.get("updated")),
                metadata={"provider": "benzinga", "query_mode": "ticker_or_topics"},
            )
            for row in rows
        ]


class FinnhubCompanyNewsCollector(BaseCollector):
    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        token = _require(self.settings.finnhub_api_key, "FINNHUB_API_KEY")
        lookback_days = int(source.config.get("lookback_days", 3))
        today = datetime.now(UTC).date()
        from_date = today - timedelta(days=max(1, lookback_days))
        data = self._get_json(
            self.settings.finnhub_base_url.rstrip("/") + "/company-news",
            params={
                "symbol": binding.ticker,
                "from": from_date.isoformat(),
                "to": today.isoformat(),
                "token": token,
            },
        )
        rows = _object_list(data)
        return [
            self._fetched(
                source=source,
                binding=binding,
                payload=row,
                provider_message_id=_str_or_none(row.get("id")),
                source_url=_str_or_none(row.get("url")),
                source_published_at=_parse_datetime(row.get("datetime")),
                metadata={"provider": "finnhub"},
            )
            for row in rows
        ]


class StocktwitsMessagesCollector(BaseCollector):
    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        rapidapi_key = self.settings.stocktwits_rapidapi_key
        if rapidapi_key:
            base_url = self.settings.stocktwits_rapidapi_base_url
            path = str(source.config.get("rapidapi_path") or "/functions/v1/stocktwits-query")
            lookback_days = max(1, int(source.config.get("lookback_days", 2)))
            today = datetime.now(UTC).date()
            params: dict[str, object | None] | None = {
                "action": source.config.get("action", "messages"),
                "symbol": binding.ticker,
                "start": (today - timedelta(days=lookback_days)).isoformat(),
                "end": today.isoformat(),
                "primaryOnly": _bool_query(source.config.get("primary_only", True)),
                "limit": min(max(1, int(source.config.get("limit", 199))), 500),
                "force_refresh": _bool_query(source.config.get("force_refresh", False)),
            }
        else:
            base_url = self.settings.stocktwits_public_base_url
            path_template = str(
                source.config.get("public_path_template")
                or source.config.get("path_template")
                or "/streams/symbol/{symbol}.json"
            )
            path = path_template.format(symbol=binding.ticker)
            params = None
        headers: dict[str, str] = {}
        if rapidapi_key:
            host = self.settings.stocktwits_rapidapi_host or urlparse(base_url).netloc
            headers = {
                "X-RapidAPI-Key": rapidapi_key,
                "X-RapidAPI-Host": host,
                "Content-Type": "application/json",
            }
        timeout = (
            float(source.config.get("timeout_seconds", 45))
            if rapidapi_key
            else self.settings.tool_http_timeout_seconds
        )
        data = self._get_json(
            base_url.rstrip("/") + path,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        rows = _stocktwits_rows(data)
        return [
            self._fetched(
                source=source,
                binding=binding,
                payload=row,
                provider_message_id=_str_or_none(row.get("id") or row.get("messageId")),
                source_url=_str_or_none(row.get("url")),
                source_published_at=_parse_datetime(row.get("created_at") or row.get("createdAt")),
                metadata={"provider": "stocktwits"},
            )
            for row in rows
        ]


class TikHubXSearchCollector(BaseCollector):
    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        token = _require(self.settings.tikhub_api_key, "TIKHUB_API_KEY")
        terms = binding.parameters.search_terms or binding.parameters.keywords
        fetched: list[FetchedExternalMessage] = []
        term_errors: list[str] = []
        for term in terms:
            try:
                data = self._get_json(
                    self.settings.tikhub_base_url.rstrip("/")
                    + "/api/v1/twitter/web/fetch_search_timeline",
                    params={
                        "keyword": term,
                        "search_type": source.config.get("search_type", "Latest"),
                        "cursor": binding.parameters.extra.get("cursor"),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPStatusError as exc:
                body = exc.response.text.replace("\n", " ")[:500]
                term_errors.append(
                    f"{term}: HTTP {exc.response.status_code} {body}"
                )
                continue
            for row in _tikhub_rows(data):
                fetched.append(
                    self._fetched(
                        source=source,
                        binding=binding,
                        payload=row,
                        provider_message_id=_tikhub_id(row),
                        source_url=_str_or_none(row.get("url") or row.get("expanded_url")),
                        source_published_at=_parse_datetime(
                            row.get("created_at") or row.get("createdAt")
                        ),
                        metadata={"provider": "tikhub", "search_term": term},
                    )
                )
        if term_errors and not fetched:
            raise CollectorError(
                "TikHub X search failed for all terms: " + " | ".join(term_errors)
            )
        return fetched


class TikHubXUserPostsCollector(BaseCollector):
    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        token = _require(self.settings.tikhub_api_key, "TIKHUB_API_KEY")
        fetched: list[FetchedExternalMessage] = []
        for username in binding.parameters.usernames:
            data = self._get_json(
                self.settings.tikhub_base_url.rstrip("/")
                + "/api/v1/twitter/web/fetch_user_post_tweet",
                params={
                    "screen_name": username.lstrip("@"),
                    "cursor": binding.parameters.extra.get("cursor"),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            for row in _tikhub_rows(data):
                fetched.append(
                    self._fetched(
                        source=source,
                        binding=binding,
                        payload=row,
                        provider_message_id=_tikhub_id(row),
                        source_url=_str_or_none(row.get("url") or row.get("expanded_url")),
                        source_published_at=_parse_datetime(
                            row.get("created_at") or row.get("createdAt")
                        ),
                        metadata={"provider": "tikhub", "username": username},
                    )
                )
        return fetched


class RSSFeedCollector(BaseCollector):
    def collect(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
    ) -> list[FetchedExternalMessage]:
        fetched: list[FetchedExternalMessage] = []
        for rss_url in binding.parameters.rss_urls:
            xml_text = self._get_text(
                rss_url,
                headers={"User-Agent": self.settings.monitoring_rss_user_agent},
            )
            for item in _parse_rss_items(xml_text, rss_url):
                fetched.append(
                    self._fetched(
                        source=source,
                        binding=binding,
                        payload=item,
                        provider_message_id=_str_or_none(item.get("guid") or item.get("id")),
                        source_url=_str_or_none(item.get("link") or item.get("id")),
                        source_published_at=_parse_datetime(
                            item.get("published") or item.get("pubDate") or item.get("updated")
                        ),
                        metadata={"provider": "rss", "rss_url": rss_url},
                    )
                )
        return fetched


def _require(value: str | None, env_name: str) -> str:
    if not value:
        raise MissingCredentialError(f"{env_name} is required for this monitoring source.")
    return value


def _bool_query(value: object) -> str:
    if isinstance(value, str):
        return "false" if value.strip().lower() in {"0", "false", "no", "off"} else "true"
    return "true" if bool(value) else "false"


def _object_list(value: object) -> list[JsonObject]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("data", "items", "messages", "results", "news"):
            child = value.get(key)
            if isinstance(child, list):
                return [dict(item) for item in child if isinstance(item, dict)]
        return [dict(value)]
    return []


def _dedupe_rows_by_provider_id(rows: list[JsonObject]) -> list[JsonObject]:
    deduped: list[JsonObject] = []
    seen: set[str] = set()
    for row in rows:
        key = _str_or_none(row.get("id")) or _str_or_none(row.get("url")) or str(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _benzinga_row_matches(row: JsonObject, ticker: str, topic: str) -> bool:
    normalized_ticker = ticker.strip().upper()
    stocks = row.get("stocks")
    if isinstance(stocks, list):
        for stock in stocks:
            if (
                isinstance(stock, dict)
                and str(stock.get("name") or "").upper() == normalized_ticker
            ):
                return True
    needle = topic.strip().lower()
    if not needle:
        return False
    text_parts: list[str] = []
    for key in ("title", "teaser", "body"):
        value = row.get(key)
        if value:
            text_parts.append(str(value))
    for key in ("tags", "channels"):
        values = row.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict) and value.get("name"):
                    text_parts.append(str(value["name"]))
    return needle in " ".join(text_parts).lower()


def _stocktwits_rows(value: object) -> list[JsonObject]:
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list):
            return [dict(item) for item in messages if isinstance(item, dict)]
    return _object_list(value)


def _tikhub_rows(value: object) -> list[JsonObject]:
    candidates = _find_social_items(value, depth=0)
    if candidates:
        return candidates
    return _object_list(value)


def _find_social_items(value: object, *, depth: int) -> list[JsonObject]:
    if depth > 8:
        return []
    if isinstance(value, list):
        rows: list[JsonObject] = []
        for item in value:
            rows.extend(_find_social_items(item, depth=depth + 1))
        return rows
    if not isinstance(value, dict):
        return []
    if _looks_like_social_message(value):
        return [dict(value)]
    for key in (
        "tweets",
        "tweet",
        "statuses",
        "messages",
        "items",
        "entries",
        "results",
        "data",
        "instructions",
        "timeline",
    ):
        child = value.get(key)
        rows = _find_social_items(child, depth=depth + 1)
        if rows:
            return rows
    rows = []
    for child in value.values():
        rows.extend(_find_social_items(child, depth=depth + 1))
        if rows:
            return rows
    return rows


def _looks_like_social_message(value: dict[str, Any]) -> bool:
    keys = set(value)
    if {"id", "text"} <= keys or {"id", "full_text"} <= keys:
        return True
    if {"rest_id", "legacy"} <= keys:
        return True
    return bool(
        ("created_at" in keys or "createdAt" in keys)
        and ("text" in keys or "body" in keys)
    )


def _tikhub_id(value: JsonObject) -> str | None:
    legacy = value.get("legacy")
    if not isinstance(legacy, dict):
        legacy = {}
    return _str_or_none(
        value.get("id")
        or value.get("rest_id")
        or value.get("tweet_id")
        or legacy.get("id_str")
    )


def _parse_rss_items(xml_text: str, feed_url: str) -> list[JsonObject]:
    root = ElementTree.fromstring(xml_text)
    rows: list[JsonObject] = []
    for item in root.findall(".//item"):
        rows.append(_rss_node_to_object(item, feed_url))
    if rows:
        return rows
    for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        rows.append(_rss_node_to_object(entry, feed_url))
    return rows


def _rss_node_to_object(node: ElementTree.Element, feed_url: str) -> JsonObject:
    result: JsonObject = {"feed_url": feed_url}
    for child in list(node):
        key = _strip_namespace(child.tag)
        text = (child.text or "").strip()
        if key == "link" and not text:
            text = child.attrib.get("href", "")
        if key in result and text:
            result[key] = f"{result[key]} {text}"
        elif text:
            result[key] = text
    return result


def _strip_namespace(tag: str) -> str:
    return re.sub(r"^\{[^}]+\}", "", tag)


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def empty_binding(source: MonitoringSourceConfig, ticker: str) -> TickerSourceBinding:
    return TickerSourceBinding(
        binding_id=f"{ticker.upper()}:{source.source_id}",
        ticker=ticker,
        source_id=source.source_id,
        enabled=False,
    )


__all__ = [
    "CollectorError",
    "MissingCredentialError",
    "MonitoringCollector",
    "MonitoringCollectorRegistry",
]
