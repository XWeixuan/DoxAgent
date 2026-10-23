"""Bounded quality review of rollout-local public results; preserve content and receipts."""

import argparse
import asyncio
import json
from datetime import timedelta
from pathlib import Path

from doxagent.content_enrichment.quality import inspect_html
from doxagent.message_bus_v2.body_results import ArticleBodyResults
from doxagent.message_bus_v2.defense_sources import ENDPOINTS, DefenseHttp
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AuditRecord, UpdateActor, utc_now


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repo = MessageBusV2Repository("/data/bus/bus.sqlite3")
    results = ArticleBodyResults(repo)
    with repo._connect() as db:
        raw_count = db.execute(
            "SELECT COUNT(*) FROM raw_messages WHERE source_id='shephard_uncrewed'"
        ).fetchone()[0]
        if raw_count:
            raise RuntimeError("serving_messages_require_message_owner_review")
        rows = db.execute(
            "SELECT data_json FROM article_body_results WHERE status='READY' "
            "AND json_extract(data_json,'$.source_id')='shephard_uncrewed' LIMIT 30"
        ).fetchall()
        originals = [json.loads(row["data_json"]) for row in rows]
    http = DefenseHttp()
    semaphore = asyncio.Semaphore(2)

    async def review(old):
        row = {"fetch_key": old["fetch_key"], "url": old["url"], "action": "unchanged"}
        async with semaphore:
            capture = await http.fetch(old["url"], ENDPOINTS["shephard_uncrewed"])
            info = await asyncio.to_thread(
                inspect_html, capture.text, capture.url, old.get("title"), generic_extraction=False
            )
            row.update(http_status=capture.status, subscription_declared=info.subscription_article)
            if capture.status != 200 or not info.subscription_article:
                return row
            row["action"] = "would_reclassify"
            if not args.apply:
                return row
            with repo.atomic_intake() as db:
                stored = db.execute(
                    "SELECT data_json FROM article_body_results WHERE fetch_key=?",
                    (old["fetch_key"],),
                ).fetchone()
                current = json.loads(stored["data_json"])
                if current.get("fetch_owner") or current.get("execution_id") != old.get(
                    "execution_id"
                ):
                    row["action"] = "changed_during_review"
                    return row
                current.update(
                    status="FAILED",
                    outcome="PARTIAL",
                    last_error="subscription_required",
                    next_check_at=(utc_now() + timedelta(minutes=30)).isoformat(),
                    quality_review={
                        "at": utc_now().isoformat(),
                        "previous_outcome": current.get("outcome"),
                        "reason": "publisher_subscription_declaration",
                        "content_preserved": True,
                    },
                )
                current["diagnostics"] = {
                    **current.get("diagnostics", {}),
                    "outcome": "PARTIAL",
                    "public_access_free": False,
                    "reason_code": "subscription_required",
                }
                results._save(db, current)
                repo.save_audit(
                    AuditRecord(
                        entity_type="article_body_result",
                        entity_id=old["fetch_key"],
                        action="quality_reclassified",
                        actor=UpdateActor.SYSTEM,
                        reason="Public HTTP cannot verify subscriber-only article completeness",
                        payload={
                            "previous_outcome": old.get("outcome"),
                            "outcome": "PARTIAL",
                            "url": old["url"],
                            "content_preserved": True,
                        },
                    )
                )
                row["action"] = "reclassified"
        return row

    async def guarded_review(old):
        try:
            return await review(old)
        except Exception as exc:
            return {
                "fetch_key": old["fetch_key"],
                "action": "unverified",
                "error": type(exc).__name__ + ":" + str(exc)[:180],
            }

    try:
        report = {
            "apply": args.apply,
            "raw_messages": raw_count,
            "reviews": await asyncio.gather(*(guarded_review(old) for old in originals)),
        }
    finally:
        await http.close()
        repo.close()
    destination = Path("/data/ops/defense-sources-20261024/shephard-quality-review.json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(report, ensure_ascii=False))


asyncio.run(main())
