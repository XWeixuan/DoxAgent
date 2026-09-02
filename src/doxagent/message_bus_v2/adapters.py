"""SourceAdapter registry, dynamic loader and six built-in provider adapters."""

from __future__ import annotations

import importlib.util
import inspect
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx

from doxagent.crawler_plane.schema import (
    CrawlerExecutionRequest,
    CrawlerExecutionStatus,
    NetworkMode,
)
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.schema import (
    AcquisitionFailure,
    JsonObject,
    PollContext,
    PollResult,
    RawMessageInput,
    SourceAdapter,
    canonical_json,
    sha256_text,
    utc_now,
)
from doxagent.settings import DoxAgentSettings


class AdapterLoadError(RuntimeError):
    pass


class CrawlerAdapterExecutionError(RuntimeError):
    def __init__(self, execution_id: str, message: str) -> None:
        super().__init__(message)
        self.crawler_execution_id = execution_id


class AdapterRegistry:
    """Resolve built-ins or O4-managed adapter scripts.

    Dynamic refs use ``file:<relative.py>:<factory>``. The path must remain under
    ``adapter_root``. Executing that code is intentional: O4 is the authorized
    source implementer, while traversal outside the managed root is rejected.
    """

    def __init__(
        self,
        settings: DoxAgentSettings,
        *,
        adapter_root: str | Path,
        client: httpx.AsyncClient | None = None,
        crawler_plane: CrawlerPlaneService | None = None,
    ) -> None:
        self.settings = settings
        self.adapter_root = Path(adapter_root).resolve()
        self.client = client or httpx.AsyncClient(timeout=settings.tool_http_timeout_seconds)
        self._owns_client = client is None
        self.crawler_plane = crawler_plane
        self._builtins: dict[str, SourceAdapter] = {
            "benzinga_news": BenzingaNewsAdapter(settings, self.client),
            "finnhub_company_news": FinnhubCompanyNewsAdapter(settings, self.client),
            "stocktwits_messages": StocktwitsMessagesAdapter(settings, self.client),
            "tikhub_x_search": TikHubXSearchAdapter(settings, self.client),
            "tikhub_x_user_posts": TikHubXUserPostsAdapter(settings, self.client),
            "newswire_rss": NewswireRSSAdapter(settings, self.client),
        }
        self._dynamic_cache: dict[tuple[str, int], SourceAdapter] = {}

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def resolve(self, adapter_ref: str, *, source_version: int) -> SourceAdapter:
        if adapter_ref.startswith("crawler:"):
            if self.crawler_plane is None:
                raise AdapterLoadError("Crawler Plane is unavailable")
            crawler_id = adapter_ref.removeprefix("crawler:").strip()
            if not crawler_id:
                raise AdapterLoadError("crawler adapter_ref requires a crawler_id")
            return CrawlerSourceAdapter(self.crawler_plane, crawler_id)
        if adapter_ref.startswith("builtin:"):
            key = adapter_ref.removeprefix("builtin:")
            try:
                return self._builtins[key]
            except KeyError as exc:
                raise AdapterLoadError(f"unknown builtin adapter: {key}") from exc
        cache_key = (adapter_ref, source_version)
        cached = self._dynamic_cache.get(cache_key)
        if cached is not None:
            return cached
        adapter = self._load_dynamic(adapter_ref)
        self._dynamic_cache[cache_key] = adapter
        return adapter

    def _load_dynamic(self, adapter_ref: str) -> SourceAdapter:
        if not adapter_ref.startswith("file:"):
            raise AdapterLoadError("adapter_ref must use builtin: or file:")
        remainder = adapter_ref.removeprefix("file:")
        try:
            relative_path, factory_name = remainder.rsplit(":", 1)
        except ValueError as exc:
            raise AdapterLoadError("dynamic adapter_ref must be file:<path.py>:<factory>") from exc
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", factory_name):
            raise AdapterLoadError("dynamic adapter factory name is invalid")
        candidate = (self.adapter_root / relative_path).resolve()
        if candidate != self.adapter_root and self.adapter_root not in candidate.parents:
            raise AdapterLoadError("dynamic adapter path escapes the configured adapter root")
        if candidate.suffix != ".py" or not candidate.is_file():
            raise AdapterLoadError(f"dynamic adapter script not found: {relative_path}")
        module = self._load_module(candidate)
        factory = getattr(module, factory_name, None)
        if factory is None or not callable(factory):
            raise AdapterLoadError(f"dynamic adapter factory not found: {factory_name}")
        parameters = inspect.signature(factory).parameters
        kwargs: dict[str, object] = {}
        if "settings" in parameters:
            kwargs["settings"] = self.settings
        if "client" in parameters:
            kwargs["client"] = self.client
        adapter = factory(**kwargs)
        if not isinstance(adapter, SourceAdapter):
            raise AdapterLoadError("dynamic adapter does not implement async poll(PollContext)")
        return adapter

    @staticmethod
    def _load_module(path: Path) -> ModuleType:
        name = f"doxagent_message_bus_v2_dynamic_{sha256_text(str(path))[:16]}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise AdapterLoadError(f"cannot load dynamic adapter module: {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class CrawlerSourceAdapter:
    """Bridge a Crawler Plane execution into the Message Bus poll contract."""

    def __init__(self, service: CrawlerPlaneService, crawler_id: str) -> None:
        self.service = service
        self.crawler_id = crawler_id

    async def poll(self, context: PollContext) -> PollResult:
        execution = await self.service.execute(
            CrawlerExecutionRequest(
                crawler_id=self.crawler_id,
                ticker=context.ticker,
                binding_id=context.binding.binding_id,
                source_id=context.source.source_id,
                source_parameters=context.binding.source_parameters,
                poll_run_id=context.poll_run_id,
                network_mode=NetworkMode.RECORD,
            ),
            request_permit=context.request_permit,
        )
        if execution.status is not CrawlerExecutionStatus.SUCCEEDED:
            raise CrawlerAdapterExecutionError(
                execution.execution_id,
                f"Crawler Plane execution {execution.execution_id} failed: "
                f"{execution.error_code}: {execution.error_message}",
            )
        messages = [
            RawMessageInput(
                external_id=item.external_id,
                source_item_key=item.external_id,
                title=item.title,
                body=item.body,
                source=item.source,
                url=item.url,
                published_at=item.published_at,
                raw_payload=item.model_dump(mode="json"),
                metadata={
                    **item.metadata,
                    "crawler_id": execution.crawler_id,
                    "crawler_version": execution.crawler_version,
                    "crawler_execution_id": execution.execution_id,
                    "poll_run_id": execution.poll_run_id,
                    "raw_artifact_ref": item.raw_artifact_ref,
                },
            )
            for item in execution.observations
        ]
        return PollResult(
            messages=messages,
            next_checkpoint=context.checkpoint,
            acquisition_metadata={
                "poll_run_id": execution.poll_run_id,
                "crawler_execution_id": execution.execution_id,
                "crawler_id": execution.crawler_id,
                "crawler_version": execution.crawler_version,
                "crawler_artifact_refs": execution.artifact_refs,
            },
        )


class _BaseAdapter:
    def __init__(self, settings: DoxAgentSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    async def _json(
        self,
        context: PollContext,
        url: str,
        *,
        params: dict[str, object | None] | None = None,
        headers: dict[str, str] | None = None,
    ) -> object:
        async with context.request_permit():
            response = await self.client.get(
                url,
                params=cast(
                    Any,
                    {key: value for key, value in (params or {}).items() if value is not None},
                ),
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    async def _text(
        self, context: PollContext, url: str, *, headers: dict[str, str] | None = None
    ) -> str:
        async with context.request_permit():
            response = await self.client.get(url, headers=headers)
            response.raise_for_status()
            return response.text

    @staticmethod
    def _failure(
        context: PollContext,
        code: str,
        message: str,
        payload: JsonObject,
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

    def _message(
        self,
        context: PollContext,
        payload: JsonObject,
        *,
        external_id: object,
        title: object,
        body: object,
        source: object,
        url: object,
        published_at: object,
        metadata: JsonObject | None = None,
    ) -> tuple[RawMessageInput | None, AcquisitionFailure | None]:
        identifier = _text_or_none(external_id)
        normalized_url = _absolute_url(url)
        normalized_time = _datetime_or_none(published_at)
        normalized_body = _html_to_text(body)
        if not normalized_body:
            return None, self._failure(
                context, "missing_body", "provider item has no body", payload
            )
        if normalized_url is None:
            return None, self._failure(
                context, "missing_absolute_url", "provider item has no absolute URL", payload
            )
        if normalized_time is None:
            return None, self._failure(
                context, "missing_published_at", "provider item has no published_at", payload
            )
        return RawMessageInput(
            external_id=identifier,
            source_item_key=identifier,
            title=_text_or_none(title),
            body=normalized_body,
            source=_text_or_none(source),
            url=normalized_url,
            published_at=normalized_time,
            raw_payload=payload,
            metadata=metadata or {},
        ), None


class BenzingaNewsAdapter(_BaseAdapter):
    async def poll(self, context: PollContext) -> PollResult:
        token = _require(self.settings.benzinga_api_key, "BENZINGA_API_KEY")
        data = await self._json(
            context,
            self.settings.benzinga_news_base_url.rstrip("/") + "/api/v2/news",
            params={
                "token": token,
                "pageSize": 50,
                "displayOutput": "full",
                "sort": "created:desc",
                "tickers": context.ticker,
            },
            headers={"accept": "application/json"},
        )
        return self._rows(context, _rows(data))

    def _rows(self, context: PollContext, rows: list[JsonObject]) -> PollResult:
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        for row in rows:
            message, failure = self._message(
                context,
                row,
                external_id=row.get("id"),
                title=row.get("title"),
                body=row.get("body") or row.get("teaser"),
                source=row.get("author") or "Benzinga",
                url=row.get("url"),
                published_at=row.get("created") or row.get("updated"),
                metadata={"provider": "benzinga"},
            )
            (messages if message else failures).append(cast(Any, message or failure))
        return PollResult(messages=messages, failures=failures)


class FinnhubCompanyNewsAdapter(_BaseAdapter):
    async def poll(self, context: PollContext) -> PollResult:
        token = _require(self.settings.finnhub_api_key, "FINNHUB_API_KEY")
        today = utc_now().date()
        data = await self._json(
            context,
            self.settings.finnhub_base_url.rstrip("/") + "/company-news",
            params={
                "symbol": context.ticker,
                "from": (today - timedelta(days=3)).isoformat(),
                "to": today.isoformat(),
                "token": token,
            },
        )
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        for row in _rows(data):
            message, failure = self._message(
                context,
                row,
                external_id=row.get("id"),
                title=row.get("headline"),
                body=row.get("summary"),
                source=row.get("source") or "Finnhub",
                url=row.get("url"),
                published_at=row.get("datetime"),
                metadata={"provider": "finnhub", "category": row.get("category")},
            )
            (messages if message else failures).append(cast(Any, message or failure))
        return PollResult(messages=messages, failures=failures)


class StocktwitsMessagesAdapter(_BaseAdapter):
    async def poll(self, context: PollContext) -> PollResult:
        key = self.settings.stocktwits_rapidapi_key
        if key:
            url = (
                self.settings.stocktwits_rapidapi_base_url.rstrip("/")
                + "/functions/v1/stocktwits-query"
            )
            data = await self._json(
                context,
                url,
                params={"action": "messages", "symbol": context.ticker, "limit": 199},
                headers={
                    "X-RapidAPI-Key": key,
                    "X-RapidAPI-Host": self.settings.stocktwits_rapidapi_host
                    or urlparse(url).netloc,
                },
            )
        else:
            url = self.settings.stocktwits_public_base_url.rstrip(
                "/"
            ) + self.settings.stocktwits_public_path_template.format(symbol=context.ticker)
            data = await self._json(context, url)
        rows = _stocktwits_rows(data)
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        newest: str | None = None
        for row in rows:
            identifier = _text_or_none(row.get("id") or row.get("messageId"))
            user = row.get("user") if isinstance(row.get("user"), dict) else {}
            username = _text_or_none(cast(dict[str, object], user).get("username"))
            item_url = _absolute_url(row.get("url"))
            if item_url is None and identifier and username:
                item_url = f"https://stocktwits.com/{username}/message/{identifier}"
            message, failure = self._message(
                context,
                row,
                external_id=identifier,
                title=row.get("title"),
                body=row.get("body") or row.get("message"),
                source=username or "Stocktwits",
                url=item_url,
                published_at=row.get("created_at") or row.get("createdAt"),
                metadata={"provider": "stocktwits", "username": username},
            )
            if message:
                messages.append(message)
                newest = identifier or newest
            elif failure:
                failures.append(failure)
        checkpoint = {"newest_external_id": newest} if newest else context.checkpoint
        return PollResult(messages=messages, failures=failures, next_checkpoint=checkpoint)


class TikHubXSearchAdapter(_BaseAdapter):
    async def poll(self, context: PollContext) -> PollResult:
        token = _require(self.settings.tikhub_api_key, "TIKHUB_API_KEY")
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        cursors: dict[str, object] = {}
        prior = context.checkpoint.get("cursors", {})
        for term in context.binding.source_parameters.get("search_terms", []):
            data = await self._json(
                context,
                self.settings.tikhub_base_url.rstrip("/")
                + "/api/v1/twitter/web/fetch_search_timeline",
                params={
                    "keyword": term,
                    "search_type": "Latest",
                    "cursor": prior.get(term) if isinstance(prior, dict) else None,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            parsed_messages, parsed_failures = self._parse(context, _tikhub_rows(data), term)
            messages.extend(parsed_messages)
            failures.extend(parsed_failures)
            cursor = _deep_find(data, "cursor")
            if cursor:
                cursors[str(term)] = cursor
        return PollResult(
            messages=messages,
            failures=failures,
            next_checkpoint={"cursors": cursors or prior},
        )

    def _parse(
        self, context: PollContext, rows: list[JsonObject], query: str
    ) -> tuple[list[RawMessageInput], list[AcquisitionFailure]]:
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        for row in rows:
            identifier = _tikhub_id(row)
            url = _absolute_url(row.get("url") or row.get("expanded_url"))
            if url is None and identifier:
                url = f"https://x.com/i/web/status/{identifier}"
            message, failure = self._message(
                context,
                row,
                external_id=identifier,
                title=None,
                body=_tikhub_body(row),
                source=_tikhub_username(row) or "X",
                url=url,
                published_at=_tikhub_published(row),
                metadata={"provider": "tikhub", "query": query},
            )
            (messages if message else failures).append(cast(Any, message or failure))
        return messages, failures


class TikHubXUserPostsAdapter(TikHubXSearchAdapter):
    async def poll(self, context: PollContext) -> PollResult:
        token = _require(self.settings.tikhub_api_key, "TIKHUB_API_KEY")
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        cursors: dict[str, object] = {}
        prior = context.checkpoint.get("cursors", {})
        for username in context.binding.source_parameters.get("usernames", []):
            data = await self._json(
                context,
                self.settings.tikhub_base_url.rstrip("/")
                + "/api/v1/twitter/web/fetch_user_post_tweet",
                params={
                    "screen_name": str(username).lstrip("@"),
                    "cursor": prior.get(username) if isinstance(prior, dict) else None,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            parsed_messages, parsed_failures = self._parse(
                context, _tikhub_rows(data), str(username)
            )
            messages.extend(parsed_messages)
            failures.extend(parsed_failures)
            cursor = _deep_find(data, "cursor")
            if cursor:
                cursors[str(username)] = cursor
        return PollResult(
            messages=messages, failures=failures, next_checkpoint={"cursors": cursors or prior}
        )


class NewswireRSSAdapter(_BaseAdapter):
    async def poll(self, context: PollContext) -> PollResult:
        messages: list[RawMessageInput] = []
        failures: list[AcquisitionFailure] = []
        for feed_url in context.binding.source_parameters.get("rss_urls", []):
            value = await self._text(
                context,
                str(feed_url),
                headers={"User-Agent": self.settings.monitoring_rss_user_agent},
            )
            for row in _rss_rows(value, str(feed_url)):
                message, failure = self._message(
                    context,
                    row,
                    external_id=row.get("guid") or row.get("id"),
                    title=row.get("title"),
                    body=row.get("summary") or row.get("description") or row.get("title"),
                    source=row.get("source") or urlparse(str(feed_url)).netloc,
                    url=row.get("link"),
                    published_at=row.get("published") or row.get("pubDate") or row.get("updated"),
                    metadata={"provider": "rss", "feed_url": feed_url},
                )
                (messages if message else failures).append(cast(Any, message or failure))
        return PollResult(messages=messages, failures=failures)


def _require(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required for this source")
    return value


def _rows(value: object) -> list[JsonObject]:
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("data", "items", "messages", "results", "news"):
            child = value.get(key)
            if isinstance(child, list):
                return [dict(row) for row in child if isinstance(row, dict)]
    return []


def _stocktwits_rows(value: object) -> list[JsonObject]:
    if isinstance(value, dict):
        for key in ("messages", "data", "results"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, dict)]
            if isinstance(rows, dict):
                nested = _stocktwits_rows(rows)
                if nested:
                    return nested
    return _rows(value)


def _tikhub_rows(value: object) -> list[JsonObject]:
    found: list[JsonObject] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_tikhub_rows(item))
    elif isinstance(value, dict):
        if _tikhub_id(value) and _tikhub_body(value):
            found.append(dict(value))
        else:
            for child in value.values():
                if isinstance(child, (dict, list)):
                    found.extend(_tikhub_rows(child))
    unique: dict[str, JsonObject] = {}
    for row in found:
        unique[_tikhub_id(row) or sha256_text(canonical_json(row))] = row
    return list(unique.values())


def _tikhub_id(row: JsonObject) -> str | None:
    legacy: JsonObject = (
        dict(cast(dict[str, Any], row["legacy"])) if isinstance(row.get("legacy"), dict) else {}
    )
    return _text_or_none(
        row.get("id") or row.get("rest_id") or row.get("tweet_id") or legacy.get("id_str")
    )


def _tikhub_body(row: JsonObject) -> object:
    legacy: JsonObject = (
        dict(cast(dict[str, Any], row["legacy"])) if isinstance(row.get("legacy"), dict) else {}
    )
    return (
        row.get("full_text")
        or row.get("text")
        or row.get("content")
        or legacy.get("full_text")
        or legacy.get("text")
    )


def _tikhub_username(row: JsonObject) -> str | None:
    legacy: JsonObject = (
        dict(cast(dict[str, Any], row["legacy"])) if isinstance(row.get("legacy"), dict) else {}
    )
    user: JsonObject = (
        dict(cast(dict[str, Any], row["user"])) if isinstance(row.get("user"), dict) else {}
    )
    return _text_or_none(
        row.get("screen_name")
        or row.get("username")
        or legacy.get("screen_name")
        or user.get("screen_name")
        or user.get("username")
    )


def _tikhub_published(row: JsonObject) -> object:
    legacy: JsonObject = (
        dict(cast(dict[str, Any], row["legacy"])) if isinstance(row.get("legacy"), dict) else {}
    )
    return (
        row.get("created_at") or row.get("createdAt") or legacy.get("created_at") or row.get("time")
    )


def _deep_find(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return cast(object, value[key])
        for child in value.values():
            found = _deep_find(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _deep_find(child, key)
            if found is not None:
                return found
    return None


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _absolute_url(value: object) -> str | None:
    text = _text_or_none(value)
    if text is None:
        return None
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)
    if isinstance(value, (int, float)) or str(value).isdigit():
        number = float(value) if isinstance(value, (int, float)) else float(str(value))
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, UTC)
    text = str(value).strip()
    try:
        return (
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            .replace(tzinfo=datetime.fromisoformat(text.replace("Z", "+00:00")).tzinfo or UTC)
            .astimezone(UTC)
        )
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
            return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            return None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_text(value: object) -> str | None:
    text = _text_or_none(value)
    if text is None:
        return None
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return re.sub(r"\s+", " ", unescape(" ".join(parser.parts))).strip() or None


def _rss_rows(xml_text: str, feed_url: str) -> list[JsonObject]:
    root = ElementTree.fromstring(xml_text)
    rows: list[JsonObject] = []
    for item in [*root.findall(".//item"), *root.findall(".//{*}entry")]:
        row: JsonObject = {"feed_url": feed_url}
        for child in item:
            key = child.tag.rsplit("}", 1)[-1]
            text = (child.text or "").strip()
            if key == "link" and not text:
                text = child.attrib.get("href", "").strip()
            if text:
                row[key] = text
        rows.append(row)
    return rows


__all__ = [
    "AdapterLoadError",
    "AdapterRegistry",
    "BenzingaNewsAdapter",
    "CrawlerAdapterExecutionError",
    "CrawlerSourceAdapter",
    "FinnhubCompanyNewsAdapter",
    "NewswireRSSAdapter",
    "StocktwitsMessagesAdapter",
    "TikHubXSearchAdapter",
    "TikHubXUserPostsAdapter",
]
