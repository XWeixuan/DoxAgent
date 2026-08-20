"""Build a recent, full-text, duplicate-free MU corpus through the monitoring bus."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.data import document_fingerprint
from cdecr.preprocessing import preprocess_source
from cdecr.single_document_contracts import PreprocessedDocument
from doxagent.monitoring.collectors import MonitoringCollectorRegistry
from doxagent.monitoring.media_enrichment import assess_media_body
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import (
    FetchedExternalMessage,
    MonitoringParameters,
    MonitoringSourceConfig,
    TickerSourceBinding,
)
from doxagent.monitoring.service import MonitoringBusService
from doxagent.settings import DoxAgentSettings

TICKER = "MU"
MARKET = "US"
LANGUAGE = Language.EN
MAX_TEXT_CHARS = 50_000
RECENT_DAYS = 7
EXTENDED_DAYS = 14


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--enrichment-concurrency", type=int, default=8)
    return parser.parse_args()


def _json(value: object, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent, default=str)


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _row_id(message_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, message_id))


def _source_name(value: str | None, url: str | None) -> str:
    if value and value.strip():
        return value.strip()
    if url:
        host = url.split("//", 1)[-1].split("/", 1)[0].strip().lower()
        if host:
            return host
    return "unknown-media-source"


def _length_bucket(length: int) -> str:
    if length < 2_000:
        return "short"
    if length < 10_000:
        return "medium"
    return "long"


def _ingest_historical(
    service: MonitoringBusService,
    collectors: MonitoringCollectorRegistry,
    *,
    source: MonitoringSourceConfig,
    binding: TickerSourceBinding,
) -> dict[str, Any]:
    """Use the bus while preserving the original time beyond its live watermark."""

    fetched = collectors.collector_for(source).collect(source=source, binding=binding)
    collected_at = datetime.now(UTC)
    prepared: list[FetchedExternalMessage] = []
    for item in fetched:
        metadata = dict(item.metadata)
        if item.source_published_at is not None:
            metadata["historical_source_published_at"] = item.source_published_at.isoformat()
        prepared.append(
            item.model_copy(
                update={
                    "source_published_at": collected_at,
                    "metadata": metadata,
                },
                deep=True,
            )
        )
    result = service.ingest_fetched(source=source, fetched=prepared)
    return result.model_dump(mode="json")


def build(output_dir: Path, *, limit: int, enrichment_concurrency: int) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    settings = DoxAgentSettings()
    if not settings.finnhub_api_key and not settings.benzinga_api_key:
        raise RuntimeError("Neither FINNHUB_API_KEY nor BENZINGA_API_KEY is configured")

    repository = InMemoryMonitoringRepository()
    collectors = MonitoringCollectorRegistry(settings)
    service = MonitoringBusService(repository, collectors=collectors)
    sources = {item.source_id: item for item in repository.list_sources()}

    fetch_stats: dict[str, Any] = {}
    if settings.finnhub_api_key:
        finnhub = sources["finnhub_company_news"]
        repository.upsert_source(
            finnhub.model_copy(
                update={"config": {**finnhub.config, "lookback_days": EXTENDED_DAYS}},
                deep=True,
            )
        )
        binding = service.configure_ticker_source(TICKER, finnhub.source_id)
        fetch_stats[finnhub.source_id] = _ingest_historical(
            service,
            collectors,
            source=repository.get_source(finnhub.source_id) or finnhub,
            binding=binding,
        )

    if settings.benzinga_api_key:
        benzinga = sources["benzinga_news"]
        binding = service.configure_ticker_source(
            TICKER,
            benzinga.source_id,
            parameters=MonitoringParameters(
                search_terms=["Micron", "memory", "semiconductors"]
            ),
        )
        fetch_stats[benzinga.source_id] = _ingest_historical(
            service,
            collectors,
            source=benzinga,
            binding=binding,
        )

    raw_messages = repository.recent_raw_messages(ticker=TICKER, limit=10_000)
    raw_by_id = {item.raw_message_id: item for item in raw_messages}
    enrichment = service.enrich_recent_media(
        ticker=TICKER,
        limit=10_000,
        concurrency=enrichment_concurrency,
        incomplete_only=True,
        reader_fallback=True,
    )
    media_records = repository.list_media_enrichment_records(
        ticker=TICKER,
        limit=10_000,
        incomplete_only=False,
    )

    now = datetime.now(UTC)
    recent_start = now - timedelta(days=RECENT_DAYS)
    extended_start = now - timedelta(days=EXTENDED_DAYS)
    candidates: list[SourceMessage] = []
    rejected: Counter[str] = Counter()
    for record in media_records:
        raw = raw_by_id.get(record.raw_message_id)
        historical_published_at = (
            raw.metadata.get("historical_source_published_at") if raw is not None else None
        )
        published_at = (
            datetime.fromisoformat(str(historical_published_at))
            if historical_published_at
            else None
        )
        if published_at is None:
            rejected["missing_published_at"] += 1
            continue
        if published_at < extended_start or published_at > now + timedelta(hours=6):
            rejected["outside_14_day_window"] += 1
            continue
        quality = assess_media_body(record.body, record.title)
        if not quality.complete_like:
            rejected[f"body_{quality.reason}"] += 1
            continue
        title = (record.title or "").strip()
        text = (record.body or "").strip()[:MAX_TEXT_CHARS]
        url = record.url
        if not title or not text or not url:
            rejected["missing_required_source_field"] += 1
            continue
        provider_key = (
            raw.provider_message_id
            if raw is not None and raw.provider_message_id
            else url
        )
        candidates.append(
            SourceMessage(
                message_id=_stable_id(record.source_id, provider_key),
                source_type=SourceType.NEWS,
                title=title,
                text=text,
                published_at=published_at,
                source_name=_source_name(record.source_name, url),
                url=url,
                ticker_hints=[TICKER],
                parent_message_id=None,
                language=LANGUAGE,
            )
        )

    candidates.sort(key=lambda item: (item.published_at, item.message_id), reverse=True)
    accepted: list[SourceMessage] = []
    known_documents: list[PreprocessedDocument] = []
    duplicate_relations: Counter[str] = Counter()
    for source in candidates:
        preprocessing = preprocess_source(source, known_documents=known_documents)
        if preprocessing.duplicate_relations:
            for relation in preprocessing.duplicate_relations:
                duplicate_relations[relation.relation_type.value] += 1
            continue
        accepted.append(source)
        known_documents.append(preprocessing.document)

    recent = [item for item in accepted if item.published_at >= recent_start]
    pool = recent if len(recent) >= limit else accepted
    window_days = RECENT_DAYS if pool is recent else EXTENDED_DAYS
    if len(pool) > limit:
        selected = sorted(
            pool,
            key=lambda item: hashlib.sha256(item.message_id.encode("utf-8")).hexdigest(),
        )[:limit]
    else:
        selected = list(pool)
    selected.sort(key=lambda item: (item.published_at, item.message_id), reverse=True)

    snapshot_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    for source in selected:
        source_row_id = _row_id(source.message_id)
        fingerprint = document_fingerprint(source.title, source.text)
        snapshot_rows.append(
            {
                "source_row_id": source_row_id,
                "market": MARKET,
                "ticker": TICKER,
                "document_fingerprint": fingerprint,
                "message": source.model_dump(mode="json"),
            }
        )
        manifest_rows.append(
            {
                "source_row_id": source_row_id,
                "document_fingerprint": fingerprint,
                "length_bucket": _length_bucket(len(source.text)),
                "text_chars": len(source.text),
                "source_name": source.source_name,
                "expected_event_families": [],
                "review_status": "PENDING",
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "mu_recent_news_snapshot.jsonl"
    manifest_path = output_dir / "mu_recent_news_manifest.json"
    report_path = output_dir / "mu_recent_news_build_report.json"
    snapshot_path.write_text(
        "".join(_json(row) + "\n" for row in snapshot_rows), encoding="utf-8"
    )
    manifest = {
        "manifest_version": f"cdecr-mu-recent-news-v1-{now:%Y%m%d}",
        "query": {
            "market": MARKET,
            "ticker": TICKER,
            "requested_limit": limit,
            "recent_days": RECENT_DAYS,
            "extended_days": EXTENDED_DAYS,
            "selected_window_days": window_days,
            "min_complete_body_chars": 800,
            "min_complete_body_sentences": 4,
            "max_text_chars": MAX_TEXT_CHARS,
        },
        "selection": {
            "policy": (
                "message_bus_provider_dedup+full_text_enrichment+"
                "cdecr_url_text_minhash_dedup"
            ),
            "raw_bus_count": len(raw_messages),
            "body_qualified_14d": len(candidates),
            "deduplicated_14d": len(accepted),
            "deduplicated_7d": len(recent),
            "selected_count": len(selected),
        },
        "rows": manifest_rows,
    }
    manifest_path.write_text(_json(manifest, indent=2) + "\n", encoding="utf-8")
    report = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "snapshot": str(snapshot_path.resolve()),
        "manifest": str(manifest_path.resolve()),
        "fetch": fetch_stats,
        "enrichment": enrichment,
        "rejected": dict(rejected),
        "duplicate_relations": dict(duplicate_relations),
        "selection": manifest["selection"],
        "published_at_min": min((item.published_at for item in selected), default=None),
        "published_at_max": max((item.published_at for item in selected), default=None),
        "source_counts": dict(Counter(item.source_name for item in selected)),
        "body_chars": {
            "min": min((len(item.text) for item in selected), default=0),
            "max": max((len(item.text) for item in selected), default=0),
            "total": sum(len(item.text) for item in selected),
        },
    }
    report_path.write_text(_json(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    args = parse_args()
    report = build(
        args.output_dir,
        limit=args.limit,
        enrichment_concurrency=args.enrichment_concurrency,
    )
    print(_json({"selection": report["selection"], "report": str(args.output_dir)}))


if __name__ == "__main__":
    main()
