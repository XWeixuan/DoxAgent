"""Bounded, read-only DoxAtlas ``raw_media`` adapter and snapshot utilities."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.ports import RejectedSource, SourceQuery, SourceReadBatch, SourceRecord

RAW_MEDIA_COLUMNS = "id,market,ticker,published_at,source_name,title,content,url"
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


class SourceReadError(RuntimeError):
    """Safe adapter error that never includes response content or credentials."""


def _normalized_document_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split())


def document_fingerprint(title: str, text: str) -> str:
    canonical = f"{_normalized_document_text(title)}\n{_normalized_document_text(text)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def detect_language(title: str, text: str) -> Language:
    sample = f"{title}\n{text}"
    chinese = len(_CHINESE_RE.findall(sample))
    latin = len(_LATIN_RE.findall(sample))
    if chinese and chinese >= max(3, latin // 3):
        return Language.ZH
    if latin:
        return Language.EN
    return Language.UND


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("source query timestamps must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _nonempty(row: Mapping[str, Any], name: str) -> str | None:
    value = row.get(name)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def map_raw_media_row(
    row: Mapping[str, Any], *, query: SourceQuery
) -> SourceRecord | RejectedSource:
    row_id = str(row.get("id") or "unknown")
    required = {
        "id": str(row.get("id") or "").strip() or None,
        "title": _nonempty(row, "title"),
        "content": _nonempty(row, "content"),
        "published_at": _nonempty(row, "published_at"),
        "source_name": _nonempty(row, "source_name"),
        "url": _nonempty(row, "url"),
        "ticker": _nonempty(row, "ticker"),
    }
    reasons = [f"missing_{name}" for name, value in required.items() if value is None]
    content = required["content"]
    if content is not None and len(content) < query.min_text_chars:
        reasons.append("content_too_short")
    if reasons:
        return RejectedSource(source_row_id=row_id, reason_codes=reasons)

    try:
        published_at = datetime.fromisoformat(str(required["published_at"]).replace("Z", "+00:00"))
        source = SourceMessage(
            message_id=f"doxatlas:raw_media:{required['id']}",
            source_type=SourceType.NEWS,
            title=str(required["title"]),
            text=str(content),
            published_at=published_at,
            source_name=str(required["source_name"]),
            url=str(required["url"]),
            ticker_hints=[str(required["ticker"])],
            parent_message_id=None,
            language=detect_language(str(required["title"]), str(content)),
        )
    except (ValueError, ValidationError):
        return RejectedSource(source_row_id=row_id, reason_codes=["invalid_record"])
    return SourceRecord(
        source_row_id=row_id,
        market=query.market.upper(),
        ticker=query.ticker.upper(),
        document_fingerprint=document_fingerprint(source.title, source.text),
        message=source,
    )


class DoxAtlasRawMediaReader:
    """Read-only PostgREST client restricted to bounded ``raw_media`` GETs."""

    def __init__(
        self,
        *,
        supabase_url: str,
        publishable_key: str,
        timeout_seconds: float = 30.0,
        page_size: int = 200,
        client: httpx.Client | None = None,
    ) -> None:
        if not supabase_url or not publishable_key:
            raise ValueError("Supabase URL and publishable key are required")
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        self._url = f"{supabase_url.rstrip('/')}/rest/v1/raw_media"
        self._headers = {
            "apikey": publishable_key,
            "Authorization": f"Bearer {publishable_key}",
            "Accept": "application/json",
        }
        self._page_size = page_size
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DoxAtlasRawMediaReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(self, query: SourceQuery) -> SourceReadBatch:
        if query.end_at <= query.start_at:
            raise ValueError("end_at must be later than start_at")
        accepted: list[SourceRecord] = []
        rejected: list[RejectedSource] = []
        raw_count = 0
        offset = 0

        while len(accepted) < query.limit:
            page_limit = self._page_size
            params = {
                "select": RAW_MEDIA_COLUMNS,
                "market": f"eq.{query.market.lower()}",
                "ticker": f"eq.{query.ticker.upper()}",
                "and": (
                    f"(published_at.gte.{_utc_iso(query.start_at)},"
                    f"published_at.lt.{_utc_iso(query.end_at)})"
                ),
                "order": "published_at.asc,id.asc",
                "limit": str(page_limit),
                "offset": str(offset),
            }
            try:
                response = self._client.get(self._url, headers=self._headers, params=params)
            except httpx.HTTPError as exc:
                raise SourceReadError(f"Supabase request failed: {type(exc).__name__}") from exc
            if response.status_code >= 400:
                raise SourceReadError(f"Supabase request failed with HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceReadError("Supabase returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise SourceReadError("Supabase returned an unexpected JSON shape")

            raw_count += len(payload)
            for raw in payload:
                if not isinstance(raw, Mapping):
                    rejected.append(
                        RejectedSource(source_row_id="unknown", reason_codes=["invalid_json_row"])
                    )
                    continue
                mapped = map_raw_media_row(raw, query=query)
                if isinstance(mapped, SourceRecord):
                    if len(accepted) < query.limit:
                        accepted.append(mapped)
                else:
                    rejected.append(mapped)
            if len(payload) < page_limit:
                break
            offset += page_limit

        return SourceReadBatch(
            query=query,
            accepted=accepted,
            rejected=rejected,
            raw_count=raw_count,
        )


def write_snapshot(batch: SourceReadBatch, *, path: Path) -> None:
    """Write the full local snapshot. The default location is Git-ignored."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in batch.accepted:
            handle.write(record.model_dump_json() + "\n")


def build_manifest(batch: SourceReadBatch) -> dict[str, object]:
    sources = Counter(record.message.source_name for record in batch.accepted)
    rejection_reasons = Counter(
        reason for rejected in batch.rejected for reason in rejected.reason_codes
    )
    return {
        "manifest_version": 1,
        "query": batch.query.model_dump(mode="json"),
        "raw_count": batch.raw_count,
        "accepted_count": len(batch.accepted),
        "rejected_count": len(batch.rejected),
        "source_count": len(sources),
        "source_statistics": dict(sorted(sources.items())),
        "rejection_statistics": dict(sorted(rejection_reasons.items())),
        "rows": [
            {
                "source_row_id": record.source_row_id,
                "document_fingerprint": record.document_fingerprint,
            }
            for record in batch.accepted
        ],
    }


def write_manifest(batch: SourceReadBatch, *, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build_manifest(batch), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")
