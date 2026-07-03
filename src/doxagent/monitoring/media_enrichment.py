"""Supplemental full-body extraction for media monitoring messages."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import random
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from html import unescape
from typing import Any, Protocol, cast
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from doxagent.monitoring.schema import JsonObject

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 12
READER_TIMEOUT_SECONDS = 10
MAX_FINNHUB_REDIRECT_HOPS = 5
COMPLETE_BODY_MIN_CHARS = 800
COMPLETE_BODY_MIN_SENTENCES = 4
MIN_ACCEPTED_EXTRACT_CHARS = 600
JINA_READER_BASE_URL = "https://r.jina.ai/http://"

REDIRECT_QUERY_KEYS = ("url", "u", "target", "redirect", "redirect_url")
TRUNCATION_MARKERS = (
    "read more",
    "continue reading",
    "sign in to read",
    "subscribe to continue",
    "subscription required",
    "for full access",
)
POISON_PAGE_MARKERS = (
    "too many requests",
    "rate limit exceeded",
    "verify you are human",
    "checking your browser",
    "captcha",
    "access denied",
    "px-captcha",
    "oops, something went wrong",
    "enable javascript",
)
READER_DROP_MARKERS = (
    "url source:",
    "markdown content:",
    "skip to navigation",
    "skip to main content",
    "skip to right column",
    "click here to learn more",
    "this post was written by",
    "commission from our partners",
    "story continues",
    "view comments",
    "privacy policy",
    "terms of service",
)
READER_END_MARKERS = (
    "## read next",
    "### recommended for you",
    "recommended for you",
    "recommended stories",
    "recommended videos",
    "related articles",
    "more from",
    "next article",
    "continue reading",
    "## trading disclosure",
    "trading disclosure",
    "subscribe to chart art",
    "motley fool returns",
    "about this article",
)
READER_FALLBACK_HOSTS = {
    "finance.yahoo.com",
    "www.finance.yahoo.com",
    "fool.com",
    "www.fool.com",
    "seekingalpha.com",
    "www.seekingalpha.com",
    "stocktwits.com",
    "www.stocktwits.com",
    "thestreet.com",
    "www.thestreet.com",
    "benzinga.com",
    "www.benzinga.com",
    "chartmill.com",
    "www.chartmill.com",
    "qz.com",
    "www.qz.com",
    "cnbc.com",
    "www.cnbc.com",
    "proactiveinvestors.com",
    "www.proactiveinvestors.com",
}
DOMAIN_FETCH_PROFILES: dict[str, dict[str, float | int]] = {
    "finance.yahoo.com": {"concurrency": 2, "min_gap": 0.15, "jitter": 0.1},
    "www.fool.com": {"concurrency": 2, "min_gap": 0.15, "jitter": 0.1},
    "seekingalpha.com": {"concurrency": 1, "min_gap": 0.25, "jitter": 0.15},
    "stocktwits.com": {"concurrency": 1, "min_gap": 0.25, "jitter": 0.15},
    "www.thestreet.com": {"concurrency": 1, "min_gap": 0.25, "jitter": 0.15},
}
DEFAULT_FETCH_PROFILE = {"concurrency": 4, "min_gap": 0.05, "jitter": 0.05}


class AsyncResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: object
    text: str


class AsyncSessionLike(Protocol):
    async def __aenter__(self) -> AsyncSessionLike:
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool | None:
        ...

    async def get(self, url: str, **kwargs: Any) -> AsyncResponseLike:
        ...


SessionFactory = Callable[[], AsyncSessionLike]
Extractor = Callable[[str], str | None]


@dataclass(frozen=True)
class BodyQuality:
    body_length: int
    sentence_count: int
    reason: str
    complete_like: bool

    def to_payload(self) -> JsonObject:
        return {
            "body_length": self.body_length,
            "sentence_count": self.sentence_count,
            "reason": self.reason,
            "complete_like": self.complete_like,
        }


@dataclass(frozen=True)
class MediaEnrichmentRecord:
    standard_message_id: str
    raw_message_id: str
    source_id: str
    ticker: str
    title: str | None
    body: str | None
    url: str | None
    raw_url: str | None = None
    source_name: str | None = None

    @property
    def fetch_url(self) -> str | None:
        return self.url or self.raw_url


@dataclass(frozen=True)
class FetchAttempt:
    phase: str
    url: str
    domain: str | None = None
    final_url: str | None = None
    fetch_profile: str | None = None
    status_code: int | None = None
    latency_ms: int | None = None
    reason: str | None = None
    response_bytes: int | None = None

    def to_payload(self) -> JsonObject:
        return {
            "phase": self.phase,
            "url": self.url,
            "domain": self.domain,
            "final_url": self.final_url,
            "fetch_profile": self.fetch_profile,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "response_bytes": self.response_bytes,
        }


@dataclass(frozen=True)
class FetchTextResult:
    text: str
    final_url: str
    attempt: FetchAttempt


class FetchFailure(RuntimeError):
    def __init__(self, attempt: FetchAttempt) -> None:
        self.attempt = attempt
        super().__init__(attempt.reason or "fetch_failed")


@dataclass(frozen=True)
class MediaExtractionResult:
    record: MediaEnrichmentRecord
    content: str | None = None
    final_url: str | None = None
    source_name: str | None = None
    reason: str | None = None
    latency_ms: int | None = None
    fetch_profile: str | None = None
    http_status: int | None = None
    extraction_method: str | None = None
    attempts: tuple[FetchAttempt, ...] = ()
    existing_quality: BodyQuality | None = None
    extracted_quality: BodyQuality | None = None

    @property
    def succeeded(self) -> bool:
        return self.reason is None and bool(self.content)

    def to_payload(self) -> JsonObject:
        return {
            "standard_message_id": self.record.standard_message_id,
            "source_id": self.record.source_id,
            "ticker": self.record.ticker,
            "title": self.record.title,
            "final_url": self.final_url,
            "source_name": self.source_name,
            "final_domain": _host_label(self.final_url),
            "succeeded": self.succeeded,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "fetch_profile": self.fetch_profile,
            "http_status": self.http_status,
            "extraction_method": self.extraction_method,
            "attempts": [attempt.to_payload() for attempt in self.attempts],
            "content_length": len(self.content or ""),
            "existing_quality": (
                self.existing_quality.to_payload() if self.existing_quality else None
            ),
            "extracted_quality": (
                self.extracted_quality.to_payload() if self.extracted_quality else None
            ),
        }


@dataclass
class MediaEnrichmentStats:
    selected_count: int = 0
    attempted_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    written_count: int = 0
    dry_run: bool = False
    failures_by_reason: Counter[str] = field(default_factory=Counter)
    successes_by_domain: Counter[str] = field(default_factory=Counter)
    failures_by_domain: Counter[str] = field(default_factory=Counter)
    latencies_ms: list[int] = field(default_factory=list)

    def to_payload(self) -> JsonObject:
        return {
            "selected_count": self.selected_count,
            "attempted_count": self.attempted_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "written_count": self.written_count,
            "dry_run": self.dry_run,
            "failures_by_reason": dict(self.failures_by_reason),
            "successes_by_domain": dict(self.successes_by_domain),
            "failures_by_domain": dict(self.failures_by_domain),
            "latency_ms": _latency_summary(self.latencies_ms),
        }


def assess_media_body(body: str | None, title: str | None = None) -> BodyQuality:
    text = _clean_text(body or "")
    title_text = _clean_text(title or "")
    body_length = len(text)
    sentence_count = _sentence_count(text)
    lower = text.lower()

    if not text:
        return BodyQuality(body_length, sentence_count, "empty", False)
    if title_text and text.lower() == title_text.lower():
        return BodyQuality(body_length, sentence_count, "same_as_title", False)
    if _looks_like_html(text):
        return BodyQuality(body_length, sentence_count, "html_or_entity_body", False)
    if body_length < 160:
        return BodyQuality(body_length, sentence_count, "very_short", False)
    if text.endswith(("...", "…")) or any(marker in lower for marker in TRUNCATION_MARKERS):
        return BodyQuality(body_length, sentence_count, "truncated_or_paywall_marker", False)
    if body_length < COMPLETE_BODY_MIN_CHARS or sentence_count < COMPLETE_BODY_MIN_SENTENCES:
        return BodyQuality(body_length, sentence_count, "short_summary", False)
    return BodyQuality(body_length, sentence_count, "complete_like", True)


def choose_media_fetch_url(
    *,
    standard_url: str | None,
    raw_url: str | None,
    raw_payload: Mapping[str, Any] | None = None,
) -> str | None:
    payload = raw_payload or {}
    for value in (
        payload.get("url"),
        payload.get("link"),
        payload.get("canonical_url"),
        standard_url,
        raw_url,
    ):
        url = _first_url(value)
        if url:
            return url
    return None


def media_enrichment_metadata(
    existing: Mapping[str, Any],
    result: MediaExtractionResult,
) -> JsonObject:
    payload: JsonObject = dict(existing)
    attempted_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    enrichment: JsonObject = {
        "status": "success" if result.succeeded else "failed",
        "reason": result.reason,
        "attempted_at": attempted_at,
        "source_url": result.record.fetch_url,
        "final_url": result.final_url,
        "final_domain": _host_label(result.final_url),
        "source_name": result.source_name,
        "method": result.extraction_method or "curl_cffi_trafilatura",
        "extraction_method": result.extraction_method,
        "fetch_profile": result.fetch_profile,
        "http_status": result.http_status,
        "latency_ms": result.latency_ms,
        "attempts": [attempt.to_payload() for attempt in result.attempts],
        "existing_quality": (
            result.existing_quality.to_payload() if result.existing_quality else None
        ),
        "extracted_quality": (
            result.extracted_quality.to_payload() if result.extracted_quality else None
        ),
    }
    if result.content:
        enrichment["content_sha256"] = hashlib.sha256(result.content.encode()).hexdigest()
        enrichment["content_length"] = len(result.content)
    payload["media_enrichment"] = enrichment
    return payload


async def enrich_media_records(
    records: list[MediaEnrichmentRecord],
    *,
    session_factory: SessionFactory | None = None,
    extractor: Extractor | None = None,
    concurrency: int = 6,
    dry_run: bool = False,
    reader_fallback: bool = True,
) -> tuple[MediaEnrichmentStats, list[MediaExtractionResult]]:
    stats = MediaEnrichmentStats(
        selected_count=len(records),
        attempted_count=len(records),
        dry_run=dry_run,
    )
    if not records:
        return stats, []
    resolved_factory = session_factory or _default_session_factory()
    resolved_extractor = extractor or _default_extractor()
    enable_reader_fallback = reader_fallback and extractor is None
    semaphore = asyncio.Semaphore(max(1, concurrency))
    fetch_controller = DomainFetchController()

    async with resolved_factory() as session:
        tasks = [
            _extract_with_semaphore(
                record,
                session,
                resolved_extractor,
                semaphore,
                fetch_controller,
                enable_reader_fallback=enable_reader_fallback,
            )
            for record in records
        ]
        results = list(await asyncio.gather(*tasks))

    for result in results:
        domain = _host_label(result.final_url or result.record.fetch_url) or "unknown"
        if result.latency_ms is not None:
            stats.latencies_ms.append(result.latency_ms)
        if result.succeeded:
            stats.succeeded_count += 1
            stats.successes_by_domain[domain] += 1
        else:
            stats.failed_count += 1
            stats.failures_by_reason[result.reason or "unknown"] += 1
            stats.failures_by_domain[domain] += 1
    return stats, results


async def extract_media_record(
    record: MediaEnrichmentRecord,
    session: AsyncSessionLike,
    extractor: Extractor,
    *,
    fetch_controller: DomainFetchController | None = None,
    enable_reader_fallback: bool = True,
) -> MediaExtractionResult:
    started = time.monotonic()
    existing_quality = assess_media_body(record.body, record.title)
    source_url = record.fetch_url
    attempts: list[FetchAttempt] = []
    if not source_url:
        return _failed_result(
            record,
            "missing_url",
            started,
            existing_quality=existing_quality,
            attempts=attempts,
        )
    final_url = source_url
    controller = fetch_controller or DomainFetchController()
    try:
        final_url = await _resolve_fetch_url(session, source_url)
    except Exception as exc:
        return _failed_result(
            record,
            _failure_reason(exc),
            started,
            existing_quality=existing_quality,
            final_url=final_url,
            attempts=attempts,
        )

    direct_reason: str | None = "unknown"
    direct_quality: BodyQuality | None = None
    direct_status: int | None = None
    direct_profile: str | None = None
    direct_method: str | None = None
    try:
        direct = await _fetch_text(
            session,
            final_url,
            phase="direct",
            referer=_referer_for(final_url),
            controller=controller,
        )
        attempts.append(direct.attempt)
        final_url = direct.final_url
        direct_status = direct.attempt.status_code
        direct_profile = direct.attempt.fetch_profile
        extracted_content, direct_method = _extract_article_content(
            direct.text,
            final_url,
            record.title,
            extractor,
        )
        content = _clean_text(extracted_content or "")
        direct_quality = assess_media_body(content, record.title)
        direct_reason = _candidate_failure_reason(
            content=content,
            quality=direct_quality,
            url=final_url,
            title=record.title,
            existing_body=record.body,
        )
        if direct_reason is None:
            return MediaExtractionResult(
                record=record,
                content=content,
                final_url=final_url,
                source_name=_host_label(final_url) or record.source_name,
                latency_ms=_elapsed_ms(started),
                fetch_profile=direct_profile,
                http_status=direct_status,
                extraction_method=direct_method,
                attempts=tuple(attempts),
                existing_quality=existing_quality,
                extracted_quality=direct_quality,
            )
    except FetchFailure as exc:
        attempts.append(exc.attempt)
        direct_reason = exc.attempt.reason or "fetch_failed"
        direct_status = exc.attempt.status_code
        direct_profile = exc.attempt.fetch_profile

    if enable_reader_fallback and _should_try_reader_fallback(final_url, direct_reason):
        reader_result = await _try_reader_fallback(
            record=record,
            session=session,
            article_url=final_url,
            controller=controller,
            attempts=attempts,
        )
        if reader_result.succeeded:
            return replace(
                reader_result,
                latency_ms=_elapsed_ms(started),
                existing_quality=existing_quality,
            )
        direct_reason = reader_result.reason or direct_reason
        direct_quality = reader_result.extracted_quality or direct_quality
        direct_status = reader_result.http_status or direct_status
        direct_profile = reader_result.fetch_profile or direct_profile
        direct_method = reader_result.extraction_method or direct_method

    return _failed_result(
        record,
        direct_reason,
        started,
        existing_quality=existing_quality,
        extracted_quality=direct_quality,
        final_url=final_url,
        attempts=attempts,
        fetch_profile=direct_profile,
        http_status=direct_status,
        extraction_method=direct_method,
    )


async def _extract_with_semaphore(
    record: MediaEnrichmentRecord,
    session: AsyncSessionLike,
    extractor: Extractor,
    semaphore: asyncio.Semaphore,
    fetch_controller: DomainFetchController,
    *,
    enable_reader_fallback: bool,
) -> MediaExtractionResult:
    async with semaphore:
        return await extract_media_record(
            record,
            session,
            extractor,
            fetch_controller=fetch_controller,
            enable_reader_fallback=enable_reader_fallback,
        )


class DomainFetchController:
    def __init__(self) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_started_at: dict[str, float] = {}

    @asynccontextmanager
    async def enter(self, url: str, *, phase: str) -> AsyncIterator[str]:
        host = _profile_host(url)
        profile = _domain_profile(host)
        semaphore = self._semaphores.setdefault(
            host,
            asyncio.Semaphore(max(1, int(profile["concurrency"]))),
        )
        async with semaphore:
            await self._pace(host, profile)
            yield _profile_label(host, profile, phase=phase)

    async def _pace(self, host: str, profile: Mapping[str, float | int]) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            min_gap = float(profile["min_gap"])
            jitter = float(profile["jitter"])
            elapsed = now - self._last_started_at.get(host, 0.0)
            delay = max(0.0, min_gap + random.uniform(0.0, jitter) - elapsed)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_started_at[host] = time.monotonic()


async def _fetch_text(
    session: AsyncSessionLike,
    url: str,
    *,
    phase: str,
    referer: str,
    controller: DomainFetchController,
    throttle_url: str | None = None,
    timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
) -> FetchTextResult:
    throttle_target = throttle_url or url
    async with controller.enter(throttle_target, phase=phase) as fetch_profile:
        started = time.monotonic()
        domain = _host_label(throttle_target)
        try:
            response = await session.get(
                url,
                headers=_request_headers(referer=referer, url=url, phase=phase),
                allow_redirects=True,
                timeout=timeout_seconds,
            )
            text = str(response.text or "")
            status_code = int(response.status_code)
            response_url = str(response.url)
            final_url = _normalize_candidate_url(response_url, base_url=url) or response_url
            reason = f"http_{status_code}" if status_code >= 400 else None
            attempt = FetchAttempt(
                phase=phase,
                url=url,
                domain=domain,
                final_url=final_url,
                fetch_profile=fetch_profile,
                status_code=status_code,
                latency_ms=_elapsed_ms(started),
                reason=reason,
                response_bytes=len(text.encode(errors="ignore")),
            )
            if reason:
                raise FetchFailure(attempt)
            return FetchTextResult(text=text, final_url=final_url, attempt=attempt)
        except FetchFailure:
            raise
        except Exception as exc:
            attempt = FetchAttempt(
                phase=phase,
                url=url,
                domain=domain,
                fetch_profile=fetch_profile,
                latency_ms=_elapsed_ms(started),
                reason=_failure_reason(exc),
            )
            raise FetchFailure(attempt) from exc


async def _try_reader_fallback(
    *,
    record: MediaEnrichmentRecord,
    session: AsyncSessionLike,
    article_url: str,
    controller: DomainFetchController,
    attempts: list[FetchAttempt],
) -> MediaExtractionResult:
    reader_url = f"{JINA_READER_BASE_URL}{article_url}"
    reader_status: int | None = None
    reader_profile: str | None = None
    try:
        reader = await _fetch_text(
            session,
            reader_url,
            phase="reader",
            referer="https://r.jina.ai/",
            controller=controller,
            throttle_url=article_url,
            timeout_seconds=READER_TIMEOUT_SECONDS,
        )
        attempts.append(reader.attempt)
        reader_status = reader.attempt.status_code
        reader_profile = reader.attempt.fetch_profile
        reader_content = _extract_reader_markdown(reader.text, article_url, record.title)
        content = _clean_text(reader_content or "")
        quality = assess_media_body(content, record.title)
        reason = _candidate_failure_reason(
            content=content,
            quality=quality,
            url=article_url,
            title=record.title,
            existing_body=record.body,
        )
        if reason is None:
            return MediaExtractionResult(
                record=record,
                content=content,
                final_url=article_url,
                source_name=_host_label(article_url) or record.source_name,
                fetch_profile=reader_profile,
                http_status=reader_status,
                extraction_method="jina_reader_markdown",
                attempts=tuple(attempts),
                extracted_quality=quality,
            )
        return MediaExtractionResult(
            record=record,
            final_url=article_url,
            source_name=_host_label(article_url) or record.source_name,
            reason=reason,
            fetch_profile=reader_profile,
            http_status=reader_status,
            extraction_method="jina_reader_markdown",
            attempts=tuple(attempts),
            extracted_quality=quality,
        )
    except FetchFailure as exc:
        attempts.append(exc.attempt)
        return MediaExtractionResult(
            record=record,
            final_url=article_url,
            source_name=_host_label(article_url) or record.source_name,
            reason=exc.attempt.reason or "reader_fetch_failed",
            fetch_profile=exc.attempt.fetch_profile,
            http_status=exc.attempt.status_code,
            extraction_method="jina_reader_markdown",
            attempts=tuple(attempts),
        )


async def _resolve_fetch_url(session: AsyncSessionLike, url: str) -> str:
    normalized = _normalize_candidate_url(url, base_url=None)
    if normalized is None:
        raise ValueError("invalid_url")
    if not _is_finnhub_url(normalized):
        return normalized

    current = normalized
    for _ in range(MAX_FINNHUB_REDIRECT_HOPS):
        response = await session.get(
            current,
            headers=_request_headers(referer="https://finnhub.io/"),
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise ValueError(f"redirect_http_{response.status_code}")

        location = _header(response.headers, "location")
        if location:
            candidate = _normalize_candidate_url(location, base_url=current)
            if candidate and _is_external_url(candidate):
                return candidate
            if candidate and _is_finnhub_url(candidate) and candidate != current:
                current = candidate
                continue

        response_url = _normalize_candidate_url(str(response.url), base_url=current)
        if response_url and _is_external_url(response_url):
            return response_url

        candidate = _external_url_from_html(response.text, current)
        if candidate:
            return candidate
        break
    raise ValueError("unresolved_redirect")


async def _fetch_html(session: AsyncSessionLike, url: str) -> str:
    response = await session.get(
        url,
        headers=_request_headers(referer="https://www.google.com/", url=url),
        allow_redirects=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise ValueError(f"http_{response.status_code}")
    return response.text


def _extract_article_content(
    html: str,
    final_url: str,
    title: str | None,
    extractor: Extractor,
) -> tuple[str | None, str]:
    json_ld_body = _extract_json_ld_article_body(html)
    if json_ld_body:
        return json_ld_body, "json_ld_article_body"

    extracted = extractor(html)
    if extracted:
        return extracted, "trafilatura"

    html_text = _extract_html_article_text(html, title)
    if html_text:
        return html_text, "html_article_text"
    return None, "empty_extract"


def _extract_json_ld_article_body(html: str) -> str | None:
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw = unescape(match.group(1)).strip()
        raw = re.sub(r"^\s*<!--|-->\s*$", "", raw).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        body = _article_body_from_json(payload)
        if body:
            return _strip_html(body)
    return None


def _article_body_from_json(value: object) -> str | None:
    if isinstance(value, list):
        for item in value:
            body = _article_body_from_json(item)
            if body:
                return body
        return None
    if not isinstance(value, dict):
        return None
    graph = value.get("@graph")
    if isinstance(graph, list):
        body = _article_body_from_json(graph)
        if body:
            return body
    article_type = value.get("@type")
    type_values = article_type if isinstance(article_type, list) else [article_type]
    normalized_types = {str(item).lower() for item in type_values if item is not None}
    if normalized_types & {"article", "newsarticle", "blogposting", "reportageNewsArticle".lower()}:
        article_body = value.get("articleBody")
        if isinstance(article_body, str) and article_body.strip():
            return article_body
    for nested_key in ("mainEntity", "mainEntityOfPage", "article"):
        body = _article_body_from_json(value.get(nested_key))
        if body:
            return body
    return None


def _extract_html_article_text(html: str, title: str | None) -> str | None:
    cleaned = re.sub(
        r"<(script|style|noscript|svg|nav|footer|header|aside)\b.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    candidates = [
        match.group(1)
        for pattern in (
            r"<article\b[^>]*>(.*?)</article>",
            r"<main\b[^>]*>(.*?)</main>",
            r"<body\b[^>]*>(.*?)</body>",
        )
        for match in re.finditer(pattern, cleaned, flags=re.IGNORECASE | re.DOTALL)
    ]
    for candidate in candidates:
        text = _strip_html(candidate)
        if text and not _looks_like_poison_text(text) and _titles_do_not_dominate(text, title):
            return text
    return None


def _extract_reader_markdown(markdown: str, article_url: str, title: str | None) -> str | None:
    host = _host_label(article_url) or ""
    lines = [line.strip() for line in markdown.replace("\r\n", "\n").split("\n")]
    start = _reader_start_index(lines, title, host)
    if start >= len(lines):
        return None
    selected: list[str] = []
    for raw_line in lines[start:]:
        line = raw_line.strip()
        if not line:
            continue
        if _is_reader_end_line(line, host):
            break
        cleaned = _clean_reader_line(line, host)
        if not cleaned:
            continue
        selected.append(cleaned)
    text = "\n\n".join(selected)
    if _looks_like_poison_text(text) or _reader_text_has_navigation_bias(text):
        return None
    return text


def _reader_start_index(lines: list[str], title: str | None, host: str = "") -> int:
    for index, line in enumerate(lines):
        if not line.startswith("#"):
            continue
        cleaned = line.lstrip("#").strip()
        if _titles_match(cleaned, title):
            return index + 1
    for index, line in enumerate(lines):
        lower = line.lower()
        if lower.startswith(("title:", "url source:", "published time:")):
            continue
        cleaned = line.lstrip("#").strip()
        if _titles_match(cleaned, title):
            return index + 1
    if host.endswith("yahoo.com"):
        return len(lines)
    for index, line in enumerate(lines):
        if line.startswith("# ") and index > 2:
            return index + 1
    for index, line in enumerate(lines):
        lower = line.lower()
        if lower.startswith("markdown content:"):
            return index + 1
    return 0


def _is_reader_end_line(line: str, host: str) -> bool:
    lower = line.strip().lower()
    if any(lower.startswith(marker) for marker in READER_END_MARKERS):
        return True
    if host.endswith("yahoo.com") and lower.startswith(("view comments", "recommended stories")):
        return True
    if host.endswith("yahoo.com") and (
        lower.startswith("[continue reading]")
        or lower.startswith("* * *")
        or lower.startswith("## more news")
        or lower.startswith("### trending tickers")
    ):
        return True
    if host.endswith("stocktwits.com") and lower.startswith(("subscribe to", "next article")):
        return True
    return False


def _clean_reader_line(line: str, host: str) -> str | None:
    lower = line.lower()
    if lower.startswith("title:") or any(marker in lower for marker in READER_DROP_MARKERS):
        return None
    if lower.startswith(("published time:", "image:", "logo:", "favicon:")):
        return None
    if host.endswith("yahoo.com") and (
        "yahoo is using ai" in lower
        or "coinbase" in lower
        or "learn more about" in lower
        or lower == "yahoo finance"
        or lower == "more"
    ):
        return None
    if line.startswith("![") or line.startswith("[!["):
        return None
    cleaned = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", line)
    cleaned = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"^[#>*\-\s]+", "", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if re.fullmatch(r"\$?[A-Z]{1,6}(?:\s+\d+(?:\.\d+)?%?)?", cleaned):
        return None
    return cleaned


def _candidate_failure_reason(
    *,
    content: str,
    quality: BodyQuality,
    url: str,
    title: str | None,
    existing_body: str | None,
) -> str | None:
    if _is_unsupported_media_url(url):
        return "unsupported_media"
    if not content:
        return "empty_extract"
    if _looks_like_poison_text(content) or _reader_text_has_navigation_bias(content):
        return "poison_or_navigation_extract"
    source_limited_reason = _source_limited_failure_reason(content, quality, url)
    if source_limited_reason:
        return source_limited_reason
    if _titles_do_not_dominate(content, title) and _is_acceptable_enrichment(
        existing_body=existing_body,
        extracted_body=content,
        extracted_quality=quality,
    ):
        return None
    return "incomplete_extract"


def _is_unsupported_media_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    return (
        "/video/" in path
        or path.endswith("/video")
        or host.endswith("jwplayer.com")
        or (host.endswith("cnbc.com") and "/video/" in path)
    )


def _source_limited_failure_reason(
    content: str,
    quality: BodyQuality,
    url: str,
) -> str | None:
    host = _host_label(url) or ""
    if not (host.endswith("yahoo.com") or host.endswith("thestreet.com")):
        return None
    lower = content.lower()
    if quality.reason in {"very_short", "short_summary", "same_as_title"}:
        return "source_summary_only"
    if quality.reason == "truncated_or_paywall_marker" and (
        "continue reading" in lower
        or "trading disclosure" in lower
        or "for full access" in lower
    ):
        return "source_continue_reading_only"
    return None


def _should_try_reader_fallback(url: str, reason: str | None) -> bool:
    if reason is None:
        return False
    if reason not in {
        "http_403",
        "http_404",
        "http_429",
        "timeout",
        "empty_extract",
        "incomplete_extract",
        "poison_or_navigation_extract",
    }:
        return False
    host = _host_label(url)
    return bool(host and _host_matches(host, READER_FALLBACK_HOSTS))


def _external_url_from_html(html: str, base_url: str) -> str | None:
    patterns = (
        r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+content=[\"'][^\"']*url=([^\"'>\s]+)",
        r"(?:window\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)",
        r"(?:data-url|data-href|href)=[\"']([^\"']+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            candidate = _normalize_candidate_url(match.group(1), base_url=base_url)
            if candidate and _is_external_url(candidate):
                return candidate

    for match in re.finditer(r"https?://[^\s\"'<>]+", html, flags=re.IGNORECASE):
        candidate = _normalize_candidate_url(match.group(0), base_url=base_url)
        if candidate and _is_external_url(candidate):
            return candidate
    return None


def _normalize_candidate_url(
    candidate: object,
    *,
    base_url: str | None,
    depth: int = 0,
) -> str | None:
    if candidate is None or depth > 3:
        return None
    text = unescape(str(candidate)).strip().strip("\"'<> )].,")
    if not text:
        return None
    text = unquote(text)
    if text.startswith("//"):
        text = f"https:{text}"
    if base_url:
        text = urljoin(base_url, text)

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = parse_qs(parsed.query)
    for key in REDIRECT_QUERY_KEYS:
        values = query.get(key)
        if values:
            nested = _normalize_candidate_url(values[0], base_url=base_url, depth=depth + 1)
            if nested and _is_external_url(nested):
                return nested
    return text


def _is_finnhub_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "finnhub.io" or host.endswith(".finnhub.io")


def _is_external_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not _is_finnhub_url(url)


def _request_headers(
    *,
    referer: str,
    url: str | None = None,
    phase: str = "direct",
) -> dict[str, str]:
    host = _host_label(url)
    resolved_referer = referer
    if phase == "direct" and host:
        if host.endswith("yahoo.com"):
            resolved_referer = "https://finance.yahoo.com/"
        elif host.endswith("fool.com"):
            resolved_referer = "https://www.fool.com/"
        elif host.endswith("seekingalpha.com"):
            resolved_referer = "https://seekingalpha.com/"
        elif host.endswith("cnbc.com"):
            resolved_referer = "https://www.cnbc.com/"
        elif host.endswith("benzinga.com"):
            resolved_referer = "https://www.benzinga.com/"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "DNT": "1",
        "Pragma": "no-cache",
        "Referer": resolved_referer,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if phase == "reader":
        headers["Accept"] = "text/markdown,text/plain;q=0.9,*/*;q=0.8"
        headers["Sec-Fetch-Site"] = "same-origin"
    return headers


def _referer_for(url: str) -> str:
    host = _host_label(url) or ""
    if host:
        return f"https://{host}/"
    return "https://www.google.com/"


def _profile_host(url: str) -> str:
    host = _host_label(url) or "unknown"
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_profile(host: str) -> Mapping[str, float | int]:
    if host in DOMAIN_FETCH_PROFILES:
        return DOMAIN_FETCH_PROFILES[host]
    www_host = f"www.{host}"
    if www_host in DOMAIN_FETCH_PROFILES:
        return DOMAIN_FETCH_PROFILES[www_host]
    return DEFAULT_FETCH_PROFILE


def _profile_label(
    host: str,
    profile: Mapping[str, float | int],
    *,
    phase: str,
) -> str:
    return (
        f"{phase}:{host}:c{int(profile['concurrency'])}:"
        f"gap{float(profile['min_gap']):.2f}:j{float(profile['jitter']):.2f}"
    )


def _host_matches(host: str, candidates: set[str]) -> bool:
    normalized = host.lower()
    normalized_no_www = normalized[4:] if normalized.startswith("www.") else normalized
    for candidate in candidates:
        candidate_no_www = candidate[4:] if candidate.startswith("www.") else candidate
        if normalized_no_www == candidate_no_www or normalized_no_www.endswith(
            f".{candidate_no_www}"
        ):
            return True
    return False


def _strip_html(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(text)


def _looks_like_poison_text(text: str) -> bool:
    lower = _clean_text(text).lower()
    return any(marker in lower for marker in POISON_PAGE_MARKERS)


def _reader_text_has_navigation_bias(text: str) -> bool:
    lower = text.lower()
    nav_hits = sum(
        marker in lower
        for marker in (
            "trending tickers",
            "search for news",
            "sign in to view",
            "recommended for you",
            "related articles",
            "privacy dashboard",
            "terms of service",
        )
    )
    if nav_hits >= 2:
        return True
    words = re.findall(r"[A-Za-z]{3,}", text)
    links = text.count("http://") + text.count("https://")
    return bool(words and links / max(len(words), 1) > 0.05)


def _titles_do_not_dominate(content: str, title: str | None) -> bool:
    title_text = _clean_text(title or "")
    if not title_text:
        return True
    normalized_content = _normalize_title_for_match(content)
    normalized_title = _normalize_title_for_match(title_text)
    if not normalized_title:
        return True
    title_count = normalized_content.count(normalized_title)
    return title_count <= 2 or len(content) >= len(title_text) * 8


def _titles_match(candidate: str, title: str | None) -> bool:
    if not title:
        return False
    left = _normalize_title_for_match(candidate)
    right = _normalize_title_for_match(title)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _normalize_title_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unescape(value).lower())


def _default_session_factory() -> SessionFactory:
    requests_module = importlib.import_module("curl_cffi.requests")
    session_cls = cast(Any, requests_module).AsyncSession

    def factory() -> AsyncSessionLike:
        return cast(
            AsyncSessionLike,
            session_cls(
                timeout=REQUEST_TIMEOUT_SECONDS,
                impersonate="chrome",
                headers=_request_headers(referer="https://www.google.com/"),
            ),
        )

    return factory


def _default_extractor() -> Extractor:
    try:
        trafilatura = importlib.import_module("trafilatura")
    except ModuleNotFoundError:
        return lambda html: _extract_html_article_text(html, None)
    extract = cast(Any, trafilatura).extract

    def extractor(html: str) -> str | None:
        value = extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if value is None:
            return None
        return str(value)

    return extractor


def _is_acceptable_enrichment(
    *,
    existing_body: str | None,
    extracted_body: str,
    extracted_quality: BodyQuality,
) -> bool:
    if extracted_quality.complete_like:
        return True
    existing_length = len(_clean_text(existing_body or ""))
    return (
        extracted_quality.body_length >= MIN_ACCEPTED_EXTRACT_CHARS
        and extracted_quality.sentence_count >= COMPLETE_BODY_MIN_SENTENCES
        and extracted_quality.body_length >= max(existing_length * 2, MIN_ACCEPTED_EXTRACT_CHARS)
        and extracted_quality.reason != "html_or_entity_body"
    )


def _failed_result(
    record: MediaEnrichmentRecord,
    reason: str,
    started: float,
    *,
    existing_quality: BodyQuality | None = None,
    extracted_quality: BodyQuality | None = None,
    final_url: str | None = None,
    fetch_profile: str | None = None,
    http_status: int | None = None,
    extraction_method: str | None = None,
    attempts: list[FetchAttempt] | tuple[FetchAttempt, ...] = (),
) -> MediaExtractionResult:
    return MediaExtractionResult(
        record=record,
        final_url=final_url,
        source_name=_host_label(final_url) or record.source_name,
        reason=reason,
        latency_ms=_elapsed_ms(started),
        fetch_profile=fetch_profile,
        http_status=http_status,
        extraction_method=extraction_method,
        attempts=tuple(attempts),
        existing_quality=existing_quality,
        extracted_quality=extracted_quality,
    )


def _failure_reason(exc: Exception) -> str:
    text = str(exc).strip().lower()
    if not text:
        return exc.__class__.__name__.lower()
    if "unresolved_redirect" in text:
        return "unresolved_redirect"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    match = re.search(r"(?:http|redirect_http)_(\d{3})", text)
    if match:
        return f"http_{match.group(1)}"
    if "invalid_url" in text:
        return "invalid_url"
    return re.sub(r"[^a-z0-9_]+", "_", text)[:80] or "unknown"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _latency_summary(values: list[int]) -> JsonObject:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "max": ordered[-1],
    }


def _percentile(ordered_values: list[int], percentile: int) -> int:
    if not ordered_values:
        return 0
    index = min(
        len(ordered_values) - 1,
        max(0, round((percentile / 100) * (len(ordered_values) - 1))),
    )
    return ordered_values[index]


def _clean_text(value: str) -> str:
    text = unescape(value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"</?(?:p|a|div|span|strong|em|br)\b", text, flags=re.IGNORECASE))


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[.!?。！？]+", text) if part.strip()])


def _first_url(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _normalize_candidate_url(value, base_url=None)
    return None


def _host_label(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


__all__ = [
    "BodyQuality",
    "Extractor",
    "MediaEnrichmentRecord",
    "MediaEnrichmentStats",
    "MediaExtractionResult",
    "SessionFactory",
    "assess_media_body",
    "choose_media_fetch_url",
    "enrich_media_records",
    "extract_media_record",
    "media_enrichment_metadata",
]
