"""Isolated 14-day historical news staging, qualification, deduplication, and sampling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.data import document_fingerprint
from cdecr.preprocessing import preprocess_source
from cdecr.single_document_contracts import PreprocessedDocument
from doxagent.cdecr_integration.contracts import HistoricalLoadReport
from doxagent.monitoring.media_enrichment import (
    MediaEnrichmentRecord,
    assess_media_body,
    enrich_media_records,
)
from doxagent.monitoring.normalizer import normalize_message
from doxagent.monitoring.schema import (
    FetchedExternalMessage,
    MonitoringSourceConfig,
    RawExternalMessage,
    StandardMessage,
    TickerSourceBinding,
    binding_id_for,
    dedupe_key_for,
    default_source_configs,
    payload_hash,
)
from doxagent.settings import DoxAgentSettings

MAX_HISTORICAL_SOURCES = 500
MAX_TEXT_CHARS = 50_000
DEFAULT_HISTORICAL_REQUEST_GAP_SECONDS = 10.0
TRACKING_QUERY_KEYS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref"}
)


class HistoricalNewsProvider(Protocol):
    provider_id: str

    def fetch(
        self, *, ticker: str, window_start: datetime, window_end: datetime
    ) -> Sequence[FetchedExternalMessage]: ...


class HistoricalStagingRepository:
    """A job-scoped cache that never creates Message Bus event-stream rows."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS historical_candidates (
                    staging_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    provider_message_id TEXT,
                    normalized_url TEXT,
                    source_fingerprint TEXT,
                    status TEXT NOT NULL,
                    rejection_reason TEXT,
                    fetched_json TEXT NOT NULL,
                    source_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_historical_provider_id
                    ON historical_candidates(provider_id, provider_message_id)
                    WHERE provider_message_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_historical_url
                    ON historical_candidates(normalized_url);
                CREATE INDEX IF NOT EXISTS idx_historical_fingerprint
                    ON historical_candidates(source_fingerprint);
                """
            )

    def save_fetched(self, item: FetchedExternalMessage) -> str:
        identity = (
            f"{item.source_id}:provider:{item.provider_message_id}"
            if item.provider_message_id
            else f"{item.source_id}:payload:{payload_hash(item.raw_payload)}"
        )
        staging_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO historical_candidates(
                    staging_id,provider_id,provider_message_id,status,fetched_json,updated_at
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(staging_id) DO UPDATE SET
                    fetched_json=excluded.fetched_json,
                    updated_at=excluded.updated_at
                """,
                (
                    staging_id,
                    item.source_id,
                    item.provider_message_id,
                    "FETCHED",
                    item.model_dump_json(),
                    now,
                ),
            )
        return staging_id

    def qualify(
        self,
        staging_id: str,
        *,
        source: SourceMessage | None,
        normalized_url: str | None,
        fingerprint: str | None,
        status: str,
        rejection_reason: str | None = None,
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                UPDATE historical_candidates
                SET normalized_url=?, source_fingerprint=?, status=?, rejection_reason=?,
                    source_json=?, updated_at=?
                WHERE staging_id=?
                """,
                (
                    normalized_url,
                    fingerprint,
                    status,
                    rejection_reason,
                    source.model_dump_json() if source else None,
                    datetime.now(UTC).isoformat(),
                    staging_id,
                ),
            )

    def selected_sources(self) -> list[SourceMessage]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT source_json FROM historical_candidates
                WHERE status='SELECTED' AND source_json IS NOT NULL
                ORDER BY source_json
                """
            ).fetchall()
        return [SourceMessage.model_validate_json(str(row[0])) for row in rows]


class _HistoricalRequestPacer:
    """Keep sequential provider requests at least ``gap_seconds`` apart."""

    def __init__(self, gap_seconds: float) -> None:
        if gap_seconds < 0:
            raise ValueError("request_gap_seconds must be non-negative")
        self._gap_seconds = gap_seconds
        self._last_started_at: float | None = None

    def wait(self) -> None:
        if self._last_started_at is not None:
            remaining = self._gap_seconds - (time.monotonic() - self._last_started_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_started_at = time.monotonic()


class FinnhubHistoricalNewsProvider:
    provider_id = "finnhub_company_news"

    def __init__(
        self,
        settings: DoxAgentSettings,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        request_gap_seconds: float = DEFAULT_HISTORICAL_REQUEST_GAP_SECONDS,
    ) -> None:
        if not settings.finnhub_api_key:
            raise ValueError("FINNHUB_API_KEY is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.tool_http_timeout_seconds)
        self._max_retries = max_retries
        self._request_pacer = _HistoricalRequestPacer(request_gap_seconds)
        self.failed_slices: list[str] = []
        self._source = _source_config(self.provider_id)

    def fetch(
        self, *, ticker: str, window_start: datetime, window_end: datetime
    ) -> Sequence[FetchedExternalMessage]:
        binding = _binding(ticker, self.provider_id)
        rows: list[FetchedExternalMessage] = []
        self.failed_slices.clear()
        cursor = window_start.date()
        while cursor <= window_end.date():
            from doxagent.ticker_initialization.substeps import checkpointed_json

            params: dict[str, str | int | float | bool | None] = {
                "symbol": ticker.upper(),
                "from": cursor.isoformat(),
                "to": cursor.isoformat(),
                "token": self._settings.finnhub_api_key,
            }
            try:
                payload = checkpointed_json(
                    f"history:{self.provider_id}:{ticker}:{cursor.isoformat()}",
                    partial(self._get, params),
                    max_retries=0,
                )
            except Exception as exc:
                from doxagent.ticker_initialization.schema import LeaseLost

                if isinstance(exc, LeaseLost):
                    raise
                self.failed_slices.append(cursor.isoformat())
                cursor += timedelta(days=1)
                continue
            for row in _object_rows(payload):
                published_at = _parse_datetime(row.get("datetime"))
                rows.append(
                    FetchedExternalMessage(
                        source_id=self.provider_id,
                        binding_id=binding.binding_id,
                        ticker=ticker,
                        source_type=self._source.source_type,
                        interface_type=self._source.interface_type,
                        raw_payload=row,
                        provider_message_id=_optional_text(row.get("id")),
                        source_url=_optional_text(row.get("url")),
                        source_published_at=published_at,
                        metadata={"provider": "finnhub", "historical_slice": cursor.isoformat()},
                    )
                )
            cursor += timedelta(days=1)
        return rows

    def _get(self, params: dict[str, str | int | float | bool | None]) -> object:
        url = self._settings.finnhub_base_url.rstrip("/") + "/company-news"
        for attempt in range(self._max_retries + 1):
            try:
                self._request_pacer.wait()
                response = self._client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError):
                if attempt >= self._max_retries:
                    raise
        raise AssertionError("unreachable")

    def close(self) -> None:
        self._client.close()


class BenzingaHistoricalNewsProvider:
    provider_id = "benzinga_news"

    def __init__(
        self,
        settings: DoxAgentSettings,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        max_pages: int = 100,
        page_size: int = 100,
        request_gap_seconds: float = DEFAULT_HISTORICAL_REQUEST_GAP_SECONDS,
    ) -> None:
        if not settings.benzinga_api_key:
            raise ValueError("BENZINGA_API_KEY is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.tool_http_timeout_seconds)
        self._max_retries = max_retries
        self._max_pages = max_pages
        self._page_size = min(max(1, page_size), 100)
        self._request_pacer = _HistoricalRequestPacer(request_gap_seconds)
        self.failed_slices: list[str] = []
        self._source = _source_config(self.provider_id)

    def fetch(
        self, *, ticker: str, window_start: datetime, window_end: datetime
    ) -> Sequence[FetchedExternalMessage]:
        binding = _binding(ticker, self.provider_id)
        url = self._settings.benzinga_news_base_url.rstrip("/") + "/api/v2/news"
        seen: set[str] = set()
        result: list[FetchedExternalMessage] = []
        self.failed_slices.clear()
        cursor = window_start.date()
        while cursor <= window_end.date():
            day = cursor.isoformat()
            for page in range(self._max_pages):
                from doxagent.ticker_initialization.substeps import checkpointed_json

                def fetch_page(page_number: int = page, day_value: str = day) -> object:
                    for attempt in range(self._max_retries + 1):
                        try:
                            self._request_pacer.wait()
                            response = self._client.get(
                                url,
                                params={
                                    "token": self._settings.benzinga_api_key,
                                    "tickers": ticker.upper(),
                                    "dateFrom": day_value,
                                    "dateTo": day_value,
                                    "page": page_number,
                                    "pageSize": self._page_size,
                                    "displayOutput": "full",
                                    "sort": "created:desc",
                                },
                                headers={"accept": "application/json"},
                            )
                            response.raise_for_status()
                            return response.json()
                        except (httpx.HTTPError, ValueError):
                            if attempt >= self._max_retries:
                                raise
                    raise AssertionError("unreachable")

                try:
                    rows = _object_rows(
                        checkpointed_json(
                            f"history:{self.provider_id}:{ticker}:{day}:{page}",
                            fetch_page,
                            max_retries=0,
                        )
                    )
                except Exception as exc:
                    from doxagent.ticker_initialization.schema import LeaseLost

                    if isinstance(exc, LeaseLost):
                        raise
                    self.failed_slices.append(day)
                    break
                if not rows:
                    break
                new_provider_ids = 0
                for row in rows:
                    provider_id = _optional_text(row.get("id"))
                    identity = (
                        provider_id
                        or hashlib.sha256(
                            json.dumps(row, sort_keys=True, default=str).encode("utf-8")
                        ).hexdigest()
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    new_provider_ids += 1
                    published_at = _parse_datetime(row.get("created") or row.get("updated"))
                    result.append(
                        FetchedExternalMessage(
                            source_id=self.provider_id,
                            binding_id=binding.binding_id,
                            ticker=ticker,
                            source_type=self._source.source_type,
                            interface_type=self._source.interface_type,
                            raw_payload=row,
                            provider_message_id=provider_id,
                            source_url=_optional_text(row.get("url")),
                            source_published_at=published_at,
                            metadata={"provider": "benzinga", "historical_page": page},
                        )
                    )
                if new_provider_ids == 0 or len(rows) < self._page_size:
                    break
            cursor += timedelta(days=1)
        return result

    def close(self) -> None:
        self._client.close()


class HistoricalNewsLoader:
    def __init__(
        self,
        *,
        staging: HistoricalStagingRepository,
        providers: Sequence[HistoricalNewsProvider],
        sample_seed: int = 20260824,
        max_sources: int = MAX_HISTORICAL_SOURCES,
        enrichment_concurrency: int = 8,
    ) -> None:
        if max_sources < 1 or max_sources > MAX_HISTORICAL_SOURCES:
            raise ValueError("max_sources must be between 1 and 500")
        self.staging = staging
        self.providers = list(providers)
        self.sample_seed = sample_seed
        self.max_sources = max_sources
        self.enrichment_concurrency = enrichment_concurrency

    async def load(
        self, *, market: str, ticker: str, as_of: datetime
    ) -> tuple[list[SourceMessage], HistoricalLoadReport]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        normalized_ticker = ticker.strip().upper()
        window_end = as_of.astimezone(UTC)
        window_start = window_end - timedelta(days=14)
        fetched: list[tuple[str, FetchedExternalMessage]] = []
        provider_counts: Counter[str] = Counter()
        provider_failures = 0
        provider_failed_slices: Counter[str] = Counter()
        for provider in self.providers:
            try:
                items = await asyncio.to_thread(
                    provider.fetch,
                    ticker=normalized_ticker,
                    window_start=window_start,
                    window_end=window_end,
                )
            except Exception as exc:
                from doxagent.ticker_initialization.schema import LeaseLost
                from doxagent.ticker_initialization.substeps import managed

                if isinstance(exc, LeaseLost) or not managed():
                    raise
                provider_failures += 1
                continue
            failed_slices = {
                str(value) for value in getattr(provider, "failed_slices", ()) if str(value)
            }
            if failed_slices:
                provider_failed_slices[provider.provider_id] += len(failed_slices)
            provider_counts[provider.provider_id] += len(items)
            fetched.extend((self.staging.save_fetched(item), item) for item in items)
        if provider_failures and provider_failures == len(self.providers):
            raise ValueError("all historical providers are unavailable")

        normalized: list[tuple[str, FetchedExternalMessage, StandardMessage]] = []
        enrichment_records: list[MediaEnrichmentRecord] = []
        for staging_id, item in fetched:
            raw = _raw_message(staging_id, item)
            standard = normalize_message(raw, _source_config(item.source_id))
            normalized.append((staging_id, item, standard))
            if not assess_media_body(standard.body, standard.title).complete_like:
                enrichment_records.append(
                    MediaEnrichmentRecord(
                        standard_message_id=standard.standard_message_id,
                        raw_message_id=raw.raw_message_id,
                        source_id=standard.source_id,
                        ticker=normalized_ticker,
                        title=standard.title,
                        body=standard.body,
                        url=standard.url,
                        raw_url=item.source_url,
                        source_name=_source_name(standard.metadata, standard.url),
                    )
                )
        _, enrichment_results = await enrich_media_records(
            enrichment_records,
            concurrency=self.enrichment_concurrency,
            reader_fallback=True,
        )
        enrichment_by_standard = {
            item.record.standard_message_id: item for item in enrichment_results
        }

        rejected: Counter[str] = Counter()
        if provider_failures:
            rejected["unavailable_providers"] = provider_failures
        for provider_id, failed_count in provider_failed_slices.items():
            rejected[f"unavailable_days:{provider_id}"] = failed_count
        candidates: list[tuple[str, SourceMessage, str, str]] = []
        for staging_id, item, standard in normalized:
            published_at = standard.published_at
            if published_at is None or not window_start <= published_at <= window_end:
                rejected["outside_window_or_missing_time"] += 1
                self.staging.qualify(
                    staging_id,
                    source=None,
                    normalized_url=None,
                    fingerprint=None,
                    status="REJECTED",
                    rejection_reason="outside_window_or_missing_time",
                )
                continue
            if item.source_type.value == "social":
                rejected["social_source"] += 1
                self.staging.qualify(
                    staging_id,
                    source=None,
                    normalized_url=None,
                    fingerprint=None,
                    status="REJECTED",
                    rejection_reason="social_source",
                )
                continue
            symbols = {value.strip().upper() for value in standard.symbols}
            if symbols and normalized_ticker not in symbols:
                rejected["ticker_mismatch"] += 1
                self.staging.qualify(
                    staging_id,
                    source=None,
                    normalized_url=None,
                    fingerprint=None,
                    status="REJECTED",
                    rejection_reason="ticker_mismatch",
                )
                continue
            enrichment = enrichment_by_standard.get(standard.standard_message_id)
            body = standard.body
            url = standard.url
            if enrichment is not None and enrichment.succeeded:
                body = enrichment.content
                url = enrichment.final_url or url
            title = (standard.title or "").strip()
            text = (body or "").strip()[:MAX_TEXT_CHARS]
            if not title or not text or not url:
                reason = "missing_required_source_field"
            elif not assess_media_body(text, title).complete_like:
                reason = "body_not_complete_like"
            else:
                reason = ""
            if reason:
                rejected[reason] += 1
                self.staging.qualify(
                    staging_id,
                    source=None,
                    normalized_url=_normalize_url(url) if url else None,
                    fingerprint=None,
                    status="REJECTED",
                    rejection_reason=reason,
                )
                continue
            assert url is not None
            normalized_url = _normalize_url(url)
            source = SourceMessage(
                message_id=_source_message_id(item, normalized_url),
                source_type=SourceType.NEWS,
                title=title,
                text=text,
                published_at=published_at,
                source_name=_source_name(standard.metadata, url),
                url=url,
                ticker_hints=[normalized_ticker],
                parent_message_id=None,
                language=Language.EN,
            )
            fingerprint = document_fingerprint(source.title, source.text)
            candidates.append((staging_id, source, normalized_url, fingerprint))

        accepted: list[tuple[str, SourceMessage, str, str]] = []
        seen_provider: set[tuple[str, str]] = set()
        seen_urls: set[str] = set()
        known_documents: list[PreprocessedDocument] = []
        duplicate_count = 0
        for candidate in sorted(
            candidates,
            key=lambda row: (row[1].published_at, row[1].message_id),
            reverse=True,
        ):
            staging_id, source, normalized_url, fingerprint = candidate
            provider_key = (source.source_name.casefold(), source.message_id)
            preprocessing = preprocess_source(source, known_documents=known_documents)
            duplicate = (
                provider_key in seen_provider
                or normalized_url in seen_urls
                or bool(preprocessing.duplicate_relations)
            )
            if duplicate:
                duplicate_count += 1
                self.staging.qualify(
                    staging_id,
                    source=source,
                    normalized_url=normalized_url,
                    fingerprint=fingerprint,
                    status="DUPLICATE",
                    rejection_reason="provider_url_or_content_duplicate",
                )
                continue
            seen_provider.add(provider_key)
            seen_urls.add(normalized_url)
            known_documents.append(preprocessing.document)
            accepted.append(candidate)

        selected = _stratified_sample(
            [row[1] for row in accepted],
            limit=self.max_sources,
            seed=self.sample_seed,
        )
        selected_ids = {item.message_id for item in selected}
        for staging_id, source, normalized_url, fingerprint in accepted:
            self.staging.qualify(
                staging_id,
                source=source,
                normalized_url=normalized_url,
                fingerprint=fingerprint,
                status="SELECTED" if source.message_id in selected_ids else "QUALIFIED",
            )
        report = HistoricalLoadReport(
            market=market.upper(),
            ticker=normalized_ticker,
            window_start=window_start,
            window_end=window_end,
            provider_counts=dict(provider_counts),
            staged_count=len(fetched),
            qualified_count=len(accepted),
            duplicate_count=duplicate_count,
            selected_count=len(selected),
            rejected_counts=dict(rejected),
            selected_message_ids=[item.message_id for item in selected],
        )
        return selected, report


def _stratified_sample(
    values: Sequence[SourceMessage], *, limit: int, seed: int
) -> list[SourceMessage]:
    if len(values) <= limit:
        return sorted(values, key=lambda item: (item.published_at, item.message_id), reverse=True)
    strata: dict[tuple[date, str], list[SourceMessage]] = defaultdict(list)
    for item in values:
        strata[(item.published_at.date(), item.source_name.casefold())].append(item)
    for key, rows in strata.items():
        rows.sort(
            key=lambda item: hashlib.sha256(
                f"{seed}|{key[0]}|{key[1]}|{item.message_id}".encode()
            ).hexdigest()
        )
    selected: list[SourceMessage] = []
    ordered_keys = sorted(strata, key=lambda item: (item[0], item[1]), reverse=True)
    while len(selected) < limit:
        progressed = False
        for key in ordered_keys:
            if strata[key] and len(selected) < limit:
                selected.append(strata[key].pop(0))
                progressed = True
        if not progressed:
            break
    return sorted(selected, key=lambda item: (item.published_at, item.message_id), reverse=True)


def _source_config(source_id: str) -> MonitoringSourceConfig:
    sources = {item.source_id: item for item in default_source_configs()}
    try:
        return sources[source_id]
    except KeyError as exc:
        raise ValueError(f"unsupported historical provider: {source_id}") from exc


def _binding(ticker: str, source_id: str) -> TickerSourceBinding:
    return TickerSourceBinding(
        binding_id=binding_id_for(ticker, source_id),
        ticker=ticker,
        source_id=source_id,
    )


def _raw_message(staging_id: str, item: FetchedExternalMessage) -> RawExternalMessage:
    return RawExternalMessage(
        raw_message_id=f"historical:{staging_id}",
        dedupe_key=dedupe_key_for(
            source_id=item.source_id,
            provider_message_id=item.provider_message_id,
            source_url=item.source_url,
            raw_payload=item.raw_payload,
        ),
        source_id=item.source_id,
        binding_id=item.binding_id,
        ticker=item.ticker,
        source_type=item.source_type,
        interface_type=item.interface_type,
        provider_message_id=item.provider_message_id,
        payload_hash=payload_hash(item.raw_payload),
        source_url=item.source_url,
        source_published_at=item.source_published_at,
        collected_at=datetime.now(UTC),
        raw_payload=item.raw_payload,
        metadata={**item.metadata, "historical_staging": True},
    )


def _source_message_id(item: FetchedExternalMessage, normalized_url: str) -> str:
    identity = item.provider_message_id or normalized_url or payload_hash(item.raw_payload)
    digest = hashlib.sha256(f"{item.source_id}|{identity}".encode()).hexdigest()
    return f"historical:{item.source_id}:{digest}"


def _normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_QUERY_KEYS
        )
    )
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, query, ""))


def _source_name(metadata: dict[str, object], url: str | None) -> str:
    display = metadata.get("source_display_name")
    if isinstance(display, str) and display.strip():
        return display.strip()
    if url:
        host = urlsplit(url).netloc.strip().casefold()
        if host:
            return host
    return "unknown-media-source"


def _object_rows(value: object) -> list[dict[str, object]]:
    rows = value.get("data") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        return []
    return [dict(item) for item in rows if isinstance(item, dict)]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
