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

from cdecr.contracts import Language, SourceMessage
from cdecr.data import document_fingerprint
from cdecr.preprocessing import preprocess_source
from cdecr.single_document_contracts import PreprocessedDocument
from doxagent.cdecr_integration.historical_loader import (
    BenzingaHistoricalNewsProvider,
    FinnhubHistoricalNewsProvider,
    HistoricalNewsLoader,
    HistoricalStagingRepository,
)
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


def build(output_dir: Path, *, limit: int, enrichment_concurrency: int) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    settings = DoxAgentSettings()
    if not settings.finnhub_api_key and not settings.benzinga_api_key:
        raise RuntimeError("Neither FINNHUB_API_KEY nor BENZINGA_API_KEY is configured")

    import asyncio

    providers = []
    if settings.finnhub_api_key:
        providers.append(FinnhubHistoricalNewsProvider(settings))
    if settings.benzinga_api_key:
        providers.append(BenzingaHistoricalNewsProvider(settings))
    now = datetime.now(UTC)
    recent_start = now - timedelta(days=RECENT_DAYS)
    output_dir.mkdir(parents=True, exist_ok=True)
    loader = HistoricalNewsLoader(
        staging=HistoricalStagingRepository(output_dir / "historical-staging.sqlite3"),
        providers=providers,
        max_sources=500,
        enrichment_concurrency=enrichment_concurrency,
    )
    try:
        candidates, historical = asyncio.run(loader.load(market=MARKET, ticker=TICKER, as_of=now))
    finally:
        for provider in providers:
            provider.close()
    fetch_stats = historical.model_dump(mode="json")
    rejected = Counter(historical.rejected_counts)
    enrichment = {"qualified_count": historical.qualified_count}
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
    snapshot_path.write_text("".join(_json(row) + "\n" for row in snapshot_rows), encoding="utf-8")
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
                "message_bus_provider_dedup+full_text_enrichment+cdecr_url_text_minhash_dedup"
            ),
            "raw_bus_count": historical.staged_count,
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
