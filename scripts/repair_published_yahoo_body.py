"""Repair one published Yahoo article after a verified title revision.

The script reuses Message Bus content revision logic. It never creates a new
publication or changes the original article identity. Run inside the deployed
content-enrichment container with access to the Bus SQLite volume.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.message_bus_v2 import deduplication as dedup
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import (
    IngestDecision,
    RawMessageInput,
    RawProcessingStatus,
    content_hash_for,
    new_id,
)
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord, media_enrichment_metadata
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import BodyOutcome
from doxagent.site_strategy.tokens import read_token


def original_input(path: Path, raw_id: str) -> RawMessageInput:
    with sqlite3.connect(path) as db:
        row = db.execute(
            "select o.data_json from message_observations o join raw_messages r "
            "on r.ticker=o.ticker and r.source_id=o.source_id "
            "and r.identity_key=o.identity_key where r.raw_message_id=? "
            "order by o.first_seen_at limit 1",
            (raw_id,),
        ).fetchone()
    if row is None:
        raise ValueError("original provider input unavailable")
    return RawMessageInput.model_validate_json(row[0])


def counts(path: Path, raw_id: str) -> tuple[int, int]:
    with sqlite3.connect(path) as db:
        return (
            db.execute("select count(*) from raw_messages").fetchone()[0],
            db.execute(
                "select count(*) from stream_members where standard_message_id in "
                "(select standard_message_id from standard_messages where raw_message_id=?)",
                (raw_id,),
            ).fetchone()[0],
        )


def apply_revision(path: Path, raw_id: str, original: RawMessageInput, result: object) -> dict:
    repository = MessageBusV2Repository(path)
    raw = repository.get_raw(raw_id)
    if raw is None or raw.processing_status is not RawProcessingStatus.COMPLETED:
        raise ValueError("existing completed Raw required")
    if (
        raw.source_id != "yahoo_finance_news"
        or raw.ticker != "INTC"
        or raw.url != original.url
        or raw.title != original.title
        or raw.metadata.get("content_evidence", {}).get("kind") != "SUMMARY"
    ):
        raise ValueError("source, article identity or body state changed")
    if not result.succeeded or result.diagnostics.get("outcome") != "FULL":
        raise ValueError("verified FULL extraction required")
    revision = result.diagnostics.get("title_revision") or {}
    if revision.get("canonical_url") != raw.url or not revision.get("article_title"):
        raise ValueError("same-article headline revision evidence required")
    before = counts(path, raw_id)
    now = datetime.now(UTC)
    metadata = media_enrichment_metadata(raw.metadata, result)
    metadata["v2_body_completion"] = {
        "attempt_id": new_id("repair_body"),
        "started_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "succeeded": True,
        "reason": None,
        "attempt_count": 1,
    }
    metadata["body_repair"] = {
        "method": "published_content_revision",
        "previous_attempt_id": raw.metadata.get("v2_body_completion", {}).get("attempt_id"),
        "repaired_at": now.isoformat(),
    }
    enriched = original.model_copy(
        update={
            "body": result.content,
            "url": result.final_url or original.url,
            "metadata": metadata,
        }
    )
    metadata["content_evidence"] = dedup.content_evidence(enriched, original)
    candidate = raw.model_copy(
        update={
            "raw_message_id": new_id("raw"),
            "body": result.content,
            "content_hash": content_hash_for(enriched),
            "collected_at": now,
            "last_seen_at": now,
            "metadata": metadata,
        }
    )
    decision, persisted = repository.record_raw(candidate)
    updated = repository.get_raw(raw_id)
    if decision is not IngestDecision.DUPLICATE or persisted.raw_message_id != raw_id:
        raise RuntimeError("repair did not merge into the existing article")
    if updated is None or updated.metadata["content_evidence"].get("kind") != "FULL":
        raise RuntimeError("repair did not persist FULL article evidence")
    after = counts(path, raw_id)
    if after != before:
        raise RuntimeError("repair changed Raw or Stream publication count")
    with sqlite3.connect(path) as db:
        standard = db.execute(
            "select data_json from standard_messages where raw_message_id=?", (raw_id,)
        ).fetchone()
    if standard is None or json.loads(standard[0])["body"] != result.content:
        raise RuntimeError("Standard message body was not refreshed")
    return {
        "raw_id": raw_id,
        "body_chars": len(updated.body),
        "body_sha256": hashlib.sha256(updated.body.encode()).hexdigest(),
        "content_revision": updated.metadata["message_version"]["content_revision"],
        "attempt_id": updated.metadata["v2_body_completion"]["attempt_id"],
        "stream_publications": after[1],
    }


async def run(raw_id: str, apply: bool) -> None:
    settings = DoxAgentSettings()
    path = Path(settings.message_bus_v2_sqlite_path)
    original = original_input(path, raw_id)
    client = SiteAccessClient(
        settings.site_access_url,
        token=read_token(settings.site_access_worker_token, settings.site_access_worker_token_file),
    )
    extractor = SharedContentExtractor(
        site_access_client=client,
        close_site_access_client=True,
        pipeline_enabled=True,
        proxy_url=settings.crawler_egress_proxy_url,
        browser_enabled=False,
    )
    try:
        record = MediaEnrichmentRecord(
            "repair", "repair", "yahoo_finance_news", "INTC",
            original.title, original.fallback_body, original.url,
        )
        result = await extractor.extract_version(record, "body_v2.2")
    finally:
        await extractor.close()
    if not result.succeeded:
        raise RuntimeError(f"extraction failed: {result.reason}")
    backup = path.parent / "repair-backups" / f"{raw_id}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    preview = apply_revision(backup, raw_id, original, result)
    print(json.dumps({"dry_run": preview, "backup": str(backup)}, default=str), flush=True)
    if apply:
        live = apply_revision(path, raw_id, original, result)
        print(json.dumps({"applied": live}, default=str), flush=True)
        trace = result.diagnostics.get("site_access_trace") or []
        final_access = trace[-1] if trace else {}
        outcome = BodyOutcome(
            job_id=live["attempt_id"],
            final_site_id=result.diagnostics.get("site_id") or final_access.get("site_id"),
            strategy_ref=result.diagnostics.get("strategy_ref")
            or final_access.get("strategy_ref"),
            combination_id=final_access.get("combination_id"),
            outcome="FULL",
            payload={
                "source_id": "yahoo_finance_news",
                "ticker": "INTC",
                "url": original.url,
                "raw_message_id": raw_id,
                "access_trace": trace,
                "repair": True,
            },
        )
        site_client = SiteAccessClient(
            settings.site_access_url,
            token=read_token(
                settings.site_access_worker_token, settings.site_access_worker_token_file
            ),
        )
        try:
            accepted = await site_client.submit_outcomes([outcome])
            print(json.dumps({"site_outcomes_accepted": accepted}))
        finally:
            await site_client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_id")
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.raw_id, arguments.apply))
