"""Supplemental full-body extraction for media monitoring messages."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
MAX_FINNHUB_REDIRECT_HOPS = 5
COMPLETE_BODY_MIN_CHARS = 800
COMPLETE_BODY_MIN_SENTENCES = 4
MIN_ACCEPTED_EXTRACT_CHARS = 600

REDIRECT_QUERY_KEYS = ("url", "u", "target", "redirect", "redirect_url")
TRUNCATION_MARKERS = (
    "read more",
    "continue reading",
    "sign in to read",
    "subscribe to continue",
    "subscription required",
    "for full access",
)


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
class MediaExtractionResult:
    record: MediaEnrichmentRecord
    content: str | None = None
    final_url: str | None = None
    source_name: str | None = None
    reason: str | None = None
    latency_ms: int | None = None
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
            "succeeded": self.succeeded,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
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

    def to_payload(self) -> JsonObject:
        return {
            "selected_count": self.selected_count,
            "attempted_count": self.attempted_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "written_count": self.written_count,
            "dry_run": self.dry_run,
            "failures_by_reason": dict(self.failures_by_reason),
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
        "source_name": result.source_name,
        "method": "curl_cffi_trafilatura",
        "latency_ms": result.latency_ms,
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
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async with resolved_factory() as session:
        tasks = [
            _extract_with_semaphore(record, session, resolved_extractor, semaphore)
            for record in records
        ]
        results = list(await asyncio.gather(*tasks))

    for result in results:
        if result.succeeded:
            stats.succeeded_count += 1
        else:
            stats.failed_count += 1
            stats.failures_by_reason[result.reason or "unknown"] += 1
    return stats, results


async def extract_media_record(
    record: MediaEnrichmentRecord,
    session: AsyncSessionLike,
    extractor: Extractor,
) -> MediaExtractionResult:
    started = time.monotonic()
    existing_quality = assess_media_body(record.body, record.title)
    source_url = record.fetch_url
    if not source_url:
        return _failed_result(
            record,
            "missing_url",
            started,
            existing_quality=existing_quality,
        )
    final_url = source_url
    try:
        final_url = await _resolve_fetch_url(session, source_url)
        html = await _fetch_html(session, final_url)
        content = _clean_text(extractor(html) or "")
        extracted_quality = assess_media_body(content, record.title)
        if not content:
            return _failed_result(
                record,
                "empty_extract",
                started,
                existing_quality=existing_quality,
                extracted_quality=extracted_quality,
                final_url=final_url,
            )
        if not _is_acceptable_enrichment(
            existing_body=record.body,
            extracted_body=content,
            extracted_quality=extracted_quality,
        ):
            return _failed_result(
                record,
                "incomplete_extract",
                started,
                existing_quality=existing_quality,
                extracted_quality=extracted_quality,
                final_url=final_url,
            )
        return MediaExtractionResult(
            record=record,
            content=content,
            final_url=final_url,
            source_name=_host_label(final_url) or record.source_name,
            latency_ms=_elapsed_ms(started),
            existing_quality=existing_quality,
            extracted_quality=extracted_quality,
        )
    except Exception as exc:
        return _failed_result(
            record,
            _failure_reason(exc),
            started,
            existing_quality=existing_quality,
            final_url=final_url,
        )


async def _extract_with_semaphore(
    record: MediaEnrichmentRecord,
    session: AsyncSessionLike,
    extractor: Extractor,
    semaphore: asyncio.Semaphore,
) -> MediaExtractionResult:
    async with semaphore:
        return await extract_media_record(record, session, extractor)


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
        headers=_request_headers(referer="https://www.google.com/"),
        allow_redirects=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise ValueError(f"http_{response.status_code}")
    return response.text


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


def _request_headers(*, referer: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }


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
    trafilatura = importlib.import_module("trafilatura")
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
) -> MediaExtractionResult:
    return MediaExtractionResult(
        record=record,
        final_url=final_url,
        source_name=_host_label(final_url) or record.source_name,
        reason=reason,
        latency_ms=_elapsed_ms(started),
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
