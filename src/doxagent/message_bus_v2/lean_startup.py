"""One-time, audited retirement/migration before restarting the Message Bus.

Run in a rebuilt formal backend container. No network, browser, model, profile
deletion, shared-calendar changes, VACUUM, or W1/W2/W3 task mutation.
"""

import argparse
import asyncio
import json
import os
import sqlite3
from collections import Counter
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from doxagent.content_enrichment.schema import EnrichmentJob, EnrichmentJobStatus
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.content_files import ContentUnavailable

from .admission import evaluate_admission
from .live import LiveQueue
from .repository import MessageBusV2Repository
from .schema import RawMessage, RawProcessingStatus, canonical_json, utc_now
from .service import MessageBusV2Service


class StartupCleanup:
    def __init__(self, settings, receipt, *, apply=False, reason):
        self.settings, self.apply, self.reason = settings, apply, reason
        self.now = utc_now()
        self.receipt = Path(receipt)
        self.counts = Counter()
        self.repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path, initialize=False)
        self.bus = MessageBusV2Service(self.repo)
        from .news_policy import HiddenNewsIngressPolicy

        self.bus.hidden_news_policy = HiddenNewsIngressPolicy.from_strings(
            domains=getattr(settings, "message_bus_v2_hidden_news_domains", ""),
            publishers=getattr(settings, "message_bus_v2_hidden_news_publishers", ""),
        )
        self.bus.live_queue = LiveQueue(self.bus)

    def read_schema(self, path):
        if not path:
            return
        from doxagent.v2_read.repository import ReadStore

        store = ReadStore(path)
        if self.apply:
            with store.connect(write=True) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS content_search (digest TEXT PRIMARY KEY,text TEXT NOT NULL)"
                )
                db.execute("CREATE INDEX IF NOT EXISTS contents_digest ON contents(sha256)")
        self.record("read_schema", str(store.path), {}, "install_lean_search_schema")

    def captured_deltas(self):
        from .collection_plane.bridge import CaptureBridge
        from .collection_plane.store import AcquisitionStore, path_for

        path = path_for(self.repo.path)
        if not path.is_file():
            return
        store = AcquisitionStore(path)
        bridge = CaptureBridge(self.bus, store=store)
        cursor = ""
        while True:
            with store.connect() as db:
                rows = db.execute(
                    "SELECT * FROM captures WHERE status='PENDING' AND capture_id>? "
                    "ORDER BY capture_id LIMIT 8",
                    (cursor,),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                row = dict(row)
                cursor = row["capture_id"]
                with self.repo._connect() as db:
                    accepted = db.execute(
                        "SELECT 1 FROM collection_acceptances WHERE capture_id=?", (cursor,)
                    ).fetchone()
                prepared = [] if accepted else bridge.prepare(row)
                action = (
                    "ack_already_accepted_delta"
                    if accepted
                    else "accept_valid_persisted_delta"
                    if prepared
                    else "retire_expired_or_empty_delta"
                )
                self.record(
                    "capture",
                    cursor,
                    {
                        "observed_at": row["observed_at"],
                        "source_id": row["source_id"],
                        "accepted_articles": len(prepared),
                    },
                    action,
                )
                if self.apply:
                    if not accepted:
                        bridge.commit(row, prepared)
                    store.acknowledge(cursor)

    def record(self, kind, key, before, action):
        self.counts[action] += 1
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        with self.receipt.open("a", encoding="utf-8") as out:
            out.write(
                canonical_json(
                    {
                        "at": self.now.isoformat(),
                        "apply": self.apply,
                        "reason": self.reason,
                        "kind": kind,
                        "key": key,
                        "action": action,
                        "before": before,
                    }
                )
                + "\n"
            )

    def live_rechecks(self):
        cursor = ""
        while True:
            with self.repo._connect() as db:
                rows = db.execute(
                    "SELECT article_key,state,result_ref,first_seen_at,next_check_at "
                    "FROM live_articles WHERE ((work_kind='recheck' AND state IN ('FETCH_DUE','FETCHING')) "
                    "OR (state='READY' AND next_check_at<=?)) "
                    "AND article_key>? ORDER BY article_key LIMIT 32",
                    (self.now.isoformat(), cursor),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                value = dict(row)
                cursor = row["article_key"]
                action = "retire_old_recheck" if row["result_ref"] else "preserve_unfinished_body"
                self.record("live_article", cursor, value, action)
                if self.apply:
                    with self.repo.transaction() as db:
                        db.execute(
                            "UPDATE live_articles SET state=?,claim_token=NULL,lease_until=NULL,"
                            "next_check_at=?,last_error=?,work_kind=? WHERE article_key=?",
                            (
                                "READY" if row["result_ref"] else "FETCH_DUE",
                                (self.now + timedelta(minutes=30)).isoformat(),
                                "maintenance_retired_old_recheck",
                                "recheck" if row["result_ref"] else "new_article",
                                cursor,
                            ),
                        )

    async def legacy(self):
        cursor = ""
        while True:
            with self.repo._connect() as db:
                rows = db.execute(
                    "SELECT job_id,data_json AS encoded FROM content_enrichment_jobs "
                    "WHERE status IN ('queued','running','retry_wait') AND job_id>? "
                    "ORDER BY job_id LIMIT 16",
                    (cursor,),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                cursor = row["job_id"]
                try:
                    job = EnrichmentJob.model_validate_json(self.repo.content.text(row["encoded"]))
                except (OSError, ValueError, ContentUnavailable) as exc:
                    self.record(
                        "legacy_body",
                        cursor,
                        {"encoded": row["encoded"], "error": type(exc).__name__},
                        "retire_unreadable_legacy",
                    )
                    if self.apply:
                        with self.repo.transaction() as db:
                            db.execute(
                                "UPDATE content_enrichment_jobs SET status='failed',lease_expires_at=NULL,"
                                "data_json=json_set(data_json,'$.status','failed','$.claim_token',NULL,"
                                "'$.lease_expires_at',NULL,'$.result',json(?)) WHERE job_id=?",
                                (
                                    canonical_json(
                                        {
                                            "outcome": "MAINTENANCE_RETIRED",
                                            "reason": "unreadable_frozen_input",
                                        }
                                    ),
                                    cursor,
                                ),
                            )
                    continue
                expired = evaluate_admission(
                    job.message.published_at,
                    job.message.admission_context,
                    self.now,
                    job.message.publication_time_basis,
                )
                action = (
                    "retire_legacy_recheck"
                    if job.work_kind == "recheck"
                    else ("retire_expired_legacy" if expired else "migrate_valid_continuation")
                )
                self.record("legacy_body", cursor, {"encoded": row["encoded"]}, action)
                if not self.apply:
                    continue
                if action != "migrate_valid_continuation":
                    with self.repo.transaction() as db:
                        db.execute(
                            "UPDATE content_enrichment_jobs SET status='failed',lease_expires_at=NULL,"
                            "data_json=json_set(data_json,'$.status','failed','$.claim_token',NULL,"
                            "'$.lease_expires_at',NULL,'$.result',json(?)) WHERE job_id=? "
                            "AND status IN ('queued','running','retry_wait')",
                            (
                                canonical_json(
                                    {
                                        "outcome": "MAINTENANCE_RETIRED",
                                        "reason": self.reason,
                                        "classification": action,
                                        "admission": expired,
                                    }
                                ),
                                cursor,
                            ),
                        )
                    continue
                token = "migration:" + uuid4().hex
                until = self.now + timedelta(minutes=5)
                with self.repo.transaction() as db:
                    db.execute(
                        "UPDATE content_enrichment_jobs SET status='running',lease_expires_at=?,"
                        "data_json=json_set(data_json,'$.status','running','$.claim_token',?,'$.lease_expires_at',?) "
                        "WHERE job_id=?",
                        (until.isoformat(), token, until.isoformat(), cursor),
                    )
                job = job.model_copy(
                    update={
                        "status": EnrichmentJobStatus.RUNNING,
                        "claim_token": token,
                        "lease_expires_at": until,
                    }
                )
                await self.bus.live_queue.adopt(job)
        if self.apply:
            with self.repo.transaction() as db:
                remaining = db.execute(
                    "SELECT 1 FROM content_enrichment_jobs WHERE status "
                    "IN ('queued','running','retry_wait') LIMIT 1"
                ).fetchone()
                if remaining:
                    raise RuntimeError("legacy_continuations_still_pending")
                db.execute(
                    "INSERT OR REPLACE INTO live_migrations VALUES('legacy-body-complete',?)",
                    (self.now.isoformat(),),
                )

    def raw_continuations(self):
        # Already captured valid messages remain obligations, irrespective of
        # outage age. This step never refetches their body or changes first_seen.
        for status in (RawProcessingStatus.PENDING, RawProcessingStatus.PROCESSING):
            cursor = ""
            while True:
                with self.repo._connect() as db:
                    rows = db.execute(
                        "SELECT raw_message_id,data_json FROM raw_messages "
                        "WHERE processing_status=? AND raw_message_id>? "
                        "ORDER BY raw_message_id LIMIT 32",
                        (status.value, cursor),
                    ).fetchall()
                if not rows:
                    break
                for row in rows:
                    cursor = row[0]
                    raw = RawMessage.model_validate_json(row[1])
                    self.record("raw", raw.raw_message_id, {}, "finish_persisted_raw")
                    if not self.apply:
                        continue
                    if raw.bootstrap_suppressed:
                        self.repo.add_baseline(raw)
                        self.repo.save_raw(
                            raw.model_copy(
                                update={"processing_status": RawProcessingStatus.COMPLETED}
                            )
                        )
                    else:
                        standard = self.repo.get_standard_for_raw(
                            raw.raw_message_id
                        ) or self.bus._standardize(raw)
                        self.repo.finalize_standard(
                            raw=raw, message=standard, streaming=raw.streaming_config
                        )

    def historical_sweeps(self):
        path = Path(self.settings.persistent_runtime_v2_sqlite_path)
        if not path.is_file():
            return
        with sqlite3.connect(path, timeout=2) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id,kind,status,inputs,receipt FROM runtime_tasks "
                "WHERE kind IN ('SOURCE_SWEEP','SWEEP') AND status NOT IN ('SUCCEEDED','FAILED') "
                "AND json_extract(inputs,'$.cutoff')<?",
                (self.now.isoformat(),),
            ).fetchall()
        for row in rows:
            self.record("runtime_task", row["id"], dict(row), "retire_historical_sweep")
            if self.apply:
                with sqlite3.connect(path, timeout=2) as db:
                    db.execute(
                        "UPDATE runtime_tasks SET status='FAILED',owner=NULL,lease_until=NULL,"
                        "updated_at=?,receipt=json_set(receipt,'$.maintenance_retired',1,'$.error',?) "
                        "WHERE id=? AND kind IN ('SOURCE_SWEEP','SWEEP') "
                        "AND status NOT IN ('SUCCEEDED','FAILED')",
                        (self.now.isoformat(), self.reason, row["id"]),
                    )

    async def run(self, *, read_path=None):
        self.read_schema(read_path)
        if self.apply:
            self.bus.live_queue.repository.install()
        self.live_rechecks()
        await self.legacy()
        self.captured_deltas()
        self.raw_continuations()
        self.historical_sweeps()
        return dict(self.counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--read-path", default=os.environ.get("DOXAGENT_V2_READ_SQLITE_PATH"))
    args = parser.parse_args()
    cleanup = StartupCleanup(DoxAgentSettings(), args.receipt, apply=args.apply, reason=args.reason)
    print(
        json.dumps(
            asyncio.run(cleanup.run(read_path=args.read_path)), ensure_ascii=False, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
