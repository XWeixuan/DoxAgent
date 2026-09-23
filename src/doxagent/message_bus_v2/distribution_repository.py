"""Durable shared intake and per-ticker distribution state in the Bus SQLite."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from doxagent.content_enrichment.schema import EnrichmentJob

from .repository import MessageBusV2Repository
from .schema import (
    RawMessageInput,
    SourceDefinition,
    TickerSourceBinding,
    canonical_json,
    new_id,
    sha256_text,
    utc_now,
)


class DistributionRepository:
    def __init__(self, bus: MessageBusV2Repository) -> None:
        self.bus = bus
        with bus.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS distribution_runs (
                    run_id TEXT PRIMARY KEY, work_key TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL, source_json TEXT NOT NULL,
                    mode TEXT NOT NULL, window_start TEXT, cutoff TEXT,
                    checkpoint_json TEXT NOT NULL, roster_json TEXT NOT NULL,
                    status TEXT NOT NULL, coverage TEXT NOT NULL,
                    claim_token TEXT, lease_until TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS distribution_articles (
                    article_id TEXT PRIMARY KEY, article_key TEXT NOT NULL,
                    input_version TEXT NOT NULL, source_id TEXT NOT NULL,
                    original_json TEXT NOT NULL, enriched_json TEXT,
                    content_version TEXT, body_state TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                    UNIQUE(article_key,input_version)
                );
                CREATE TABLE IF NOT EXISTS distribution_observations (
                    run_id TEXT NOT NULL, article_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL, PRIMARY KEY(run_id,article_id)
                );
                CREATE TABLE IF NOT EXISTS distribution_decisions (
                    decision_id TEXT PRIMARY KEY, article_id TEXT NOT NULL,
                    content_version TEXT NOT NULL, ticker TEXT NOT NULL,
                    terms_revision INTEGER NOT NULL, classifier_version TEXT NOT NULL,
                    regex_result INTEGER NOT NULL, jev_result REAL,
                    jev_attempts INTEGER NOT NULL, error_code TEXT,
                    final_result TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(article_id,content_version,ticker,terms_revision,classifier_version)
                );
                CREATE TABLE IF NOT EXISTS distribution_deliveries (
                    delivery_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    article_id TEXT NOT NULL, ticker TEXT NOT NULL,
                    binding_json TEXT NOT NULL, terms_revision INTEGER NOT NULL,
                    admission_json TEXT NOT NULL, state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, claim_token TEXT,
                    lease_until TEXT, result_json TEXT, created_at TEXT NOT NULL,
                    UNIQUE(run_id,article_id,ticker)
                );
                CREATE INDEX IF NOT EXISTS idx_distribution_delivery_state
                    ON distribution_deliveries(state,lease_until);
            """)

    def get_or_create_run(
        self,
        *,
        work_key: str,
        source: SourceDefinition,
        mode: str,
        window_start: datetime | None,
        cutoff: datetime | None,
        roster: list[tuple[TickerSourceBinding, int, dict[str, Any]]],
    ) -> dict[str, Any]:
        run_id = "dist-" + sha256_text(work_key)[:32]
        with self.bus.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO distribution_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    work_key,
                    source.source_id,
                    canonical_json(source.model_dump(mode="json")),
                    mode,
                    window_start.isoformat() if window_start else None,
                    cutoff.isoformat() if cutoff else None,
                    "{}",
                    canonical_json(
                        [
                            {
                                "binding": binding.model_dump(mode="json"),
                                "terms_revision": revision,
                                "admission": admission,
                            }
                            for binding, revision, admission in roster
                        ]
                    ),
                    "PENDING",
                    "UNKNOWN",
                    None,
                    None,
                    utc_now().isoformat(),
                ),
            )
            row = db.execute(
                "SELECT * FROM distribution_runs WHERE work_key=?", (work_key,)
            ).fetchone()
        return dict(row)

    def claim_run(self, run_id: str, *, lease_seconds: int = 600) -> str | None:
        now = utc_now()
        token = new_id("claim")
        with self.bus.transaction() as db:
            changed = db.execute(
                "UPDATE distribution_runs SET claim_token=?,lease_until=?,status='RUNNING' "
                "WHERE run_id=? AND status NOT IN ('DONE','PARTIAL') "
                "AND (claim_token IS NULL OR lease_until<=?)",
                (
                    token,
                    (now + timedelta(seconds=lease_seconds)).isoformat(),
                    run_id,
                    now.isoformat(),
                ),
            ).rowcount
        return token if changed else None

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.bus._connect() as db:
            row = db.execute("SELECT * FROM distribution_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def release_run(self, run_id: str, token: str) -> None:
        with self.bus.transaction() as db:
            db.execute(
                "UPDATE distribution_runs SET claim_token=NULL,lease_until=NULL,status='PENDING' "
                "WHERE run_id=? AND claim_token=?",
                (run_id, token),
            )

    def attach_target(
        self, run_id: str, binding: TickerSourceBinding, revision: int, admission: dict[str, Any]
    ) -> None:
        """A later sweep owner may reuse the run while retaining its own admission context."""
        with self.bus.transaction() as db:
            run = db.execute(
                "SELECT roster_json FROM distribution_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"distribution run not found: {run_id}")
            roster = json.loads(run[0])
            if not any(item["binding"]["ticker"] == binding.ticker for item in roster):
                roster.append(
                    {
                        "binding": binding.model_dump(mode="json"),
                        "terms_revision": revision,
                        "admission": admission,
                    }
                )
                db.execute(
                    "UPDATE distribution_runs SET roster_json=? WHERE run_id=?",
                    (canonical_json(roster), run_id),
                )
            articles = db.execute(
                "SELECT o.article_id,a.body_state FROM distribution_observations o "
                "JOIN distribution_articles a USING(article_id) WHERE o.run_id=?",
                (run_id,),
            ).fetchall()
            for article in articles:
                article_id = article["article_id"]
                delivery_id = (
                    "delivery-"
                    + sha256_text(canonical_json([run_id, article_id, binding.ticker]))[:32]
                )
                state = (
                    "WAIT_BODY"
                    if article["body_state"] == "WAIT_BODY"
                    else "PENDING"
                    if article["body_state"] == "READY"
                    else "EMPTY_CONTENT"
                )
                db.execute(
                    "INSERT OR IGNORE INTO distribution_deliveries "
                    "(delivery_id,run_id,article_id,ticker,binding_json,"
                    "terms_revision,admission_json,state,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        delivery_id,
                        run_id,
                        article_id,
                        binding.ticker,
                        canonical_json(binding.model_dump(mode="json")),
                        revision,
                        canonical_json(admission),
                        state,
                        utc_now().isoformat(),
                    ),
                )

    def ingest(
        self,
        run_id: str,
        token: str,
        source: SourceDefinition,
        messages: list[RawMessageInput],
        *,
        checkpoint: dict[str, Any],
        coverage: str,
        done: bool,
        deadline_seconds: int,
        pipeline_version: str | None,
    ) -> list[str]:
        """Upsert observations and jobs before advancing the run checkpoint."""
        row = self.run(run_id)
        if row is None or row["claim_token"] != token:
            raise RuntimeError("distribution_run_lease_lost")
        job_ids: list[str] = []
        for message in messages:
            article_key = sha256_text(
                canonical_json([source.source_id, message.external_id or message.url])
            )
            input_version = sha256_text(
                canonical_json(
                    [
                        message.title,
                        message.summary,
                        message.body,
                        message.url,
                        message.raw_payload.get("updated_at")
                        if isinstance(message.raw_payload, dict)
                        else None,
                    ]
                )
            )
            article_id = "article-" + sha256_text(article_key + input_version)[:32]
            now = utc_now().isoformat()
            with self.bus.transaction() as db:
                active_run = db.execute(
                    "SELECT roster_json,claim_token FROM distribution_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if active_run is None or active_run["claim_token"] != token:
                    raise RuntimeError("distribution_run_lease_lost")
                roster = json.loads(active_run["roster_json"])
                db.execute(
                    "INSERT OR IGNORE INTO distribution_articles VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        article_id,
                        article_key,
                        input_version,
                        source.source_id,
                        canonical_json(message.model_dump(mode="json")),
                        None,
                        None,
                        "WAIT_BODY",
                        now,
                        now,
                    ),
                )
                db.execute(
                    "UPDATE distribution_articles SET last_seen_at=? WHERE article_id=?",
                    (now, article_id),
                )
                article = db.execute(
                    "SELECT body_state FROM distribution_articles WHERE article_id=?",
                    (article_id,),
                ).fetchone()
                delivery_state = (
                    "WAIT_BODY"
                    if article[0] == "WAIT_BODY"
                    else "PENDING"
                    if article[0] == "READY"
                    else "EMPTY_CONTENT"
                )
                db.execute(
                    "INSERT OR IGNORE INTO distribution_observations VALUES(?,?,?)",
                    (run_id, article_id, now),
                )
                for target in roster:
                    binding = target["binding"]
                    ticker = binding["ticker"]
                    delivery_id = (
                        "delivery-" + sha256_text(canonical_json([run_id, article_id, ticker]))[:32]
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO distribution_deliveries "
                        "(delivery_id,run_id,article_id,ticker,binding_json,"
                        "terms_revision,admission_json,state,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            delivery_id,
                            run_id,
                            article_id,
                            ticker,
                            canonical_json(binding),
                            target["terms_revision"],
                            canonical_json(target["admission"]),
                            delivery_state,
                            now,
                        ),
                    )
            with self.bus._connect() as db:
                article = db.execute(
                    "SELECT body_state FROM distribution_articles WHERE article_id=?", (article_id,)
                ).fetchone()
            if article and article[0] == "WAIT_BODY":
                intake_key = sha256_text(canonical_json(["distribution", article_id]))
                job = EnrichmentJob(
                    job_id="enrich-" + intake_key,
                    intake_key=intake_key,
                    poll_run_id=run_id,
                    source=source,
                    binding=None,
                    owner_kind="distribution_article",
                    article_id=article_id,
                    message=message,
                    created_at=utc_now(),
                    deadline_at=utc_now() + timedelta(seconds=deadline_seconds),
                    pipeline_version=pipeline_version,
                )
                self.bus.enqueue_enrichment_job(job)
                job_ids.append(job.job_id)
        with self.bus.transaction() as db:
            changed = db.execute(
                "UPDATE distribution_runs SET checkpoint_json=?,coverage=?,status=?,"
                "claim_token=NULL,lease_until=NULL "
                "WHERE run_id=? AND claim_token=?",
                (
                    canonical_json(checkpoint),
                    coverage,
                    "DONE" if done else "PENDING",
                    run_id,
                    token,
                ),
            ).rowcount
            if not changed:
                raise RuntimeError("distribution_run_lease_lost")
        return job_ids

    def finalize_article(
        self,
        job: EnrichmentJob,
        message: RawMessageInput,
        outcome_payload: dict[str, Any] | None,
    ) -> None:
        assert job.article_id
        content_version = sha256_text(
            canonical_json(
                [
                    message.title,
                    message.summary,
                    message.body,
                    message.metadata.get("media_enrichment", {}).get("outcome"),
                ]
            )
        )
        valid = bool((message.title or "").strip() or (message.summary or "").strip())
        state = "READY" if valid else "EMPTY_CONTENT"
        with self.bus.transaction() as db:
            if job.claim_token:
                self.bus._assert_enrichment_claim(db, job.job_id, job.claim_token)
            db.execute(
                "UPDATE distribution_articles SET enriched_json=?,content_version=?,"
                "body_state=? WHERE article_id=?",
                (
                    canonical_json(message.model_dump(mode="json")),
                    content_version,
                    state,
                    job.article_id,
                ),
            )
            db.execute(
                "UPDATE distribution_deliveries SET state=? WHERE article_id=? "
                "AND state='WAIT_BODY'",
                ("PENDING" if valid else "EMPTY_CONTENT", job.article_id),
            )
            if outcome_payload:
                now = utc_now().isoformat()
                db.execute(
                    "INSERT OR IGNORE INTO site_strategy_result_outbox "
                    "(outbox_id,kind,status,attempts,next_attempt_at,created_at,payload_json) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (job.job_id, "body", "PENDING", 0, now, now, canonical_json(outcome_payload)),
                )
            db.execute("DELETE FROM content_enrichment_jobs WHERE job_id=?", (job.job_id,))

    def claim_deliveries(self, *, limit: int = 16) -> list[dict[str, Any]]:
        now = utc_now()
        with self.bus.transaction() as db:
            db.execute(
                "UPDATE distribution_deliveries SET state='FAILED',claim_token=NULL,"
                "lease_until=NULL,"
                "result_json=? WHERE state='RUNNING' AND lease_until<=? AND attempts>=3",
                (canonical_json({"reason": "DELIVERY_RETRY_EXHAUSTED"}), now.isoformat()),
            )
            rows = db.execute(
                "SELECT d.*,a.enriched_json,a.original_json,a.content_version,r.source_json "
                "FROM distribution_deliveries d JOIN distribution_articles a USING(article_id) "
                "JOIN distribution_runs r USING(run_id) "
                "WHERE d.state='PENDING' OR (d.state='RUNNING' AND d.lease_until<=?) "
                "ORDER BY d.created_at LIMIT ?",
                (now.isoformat(), limit),
            ).fetchall()
            claimed = []
            for row in rows:
                token = new_id("claim")
                db.execute(
                    "UPDATE distribution_deliveries SET state='RUNNING',claim_token=?,"
                    "lease_until=?,attempts=attempts+1 WHERE delivery_id=?",
                    (token, (now + timedelta(seconds=90)).isoformat(), row["delivery_id"]),
                )
                claimed.append({**dict(row), "claim_token": token})
        return claimed

    def finish_delivery(
        self, delivery_id: str, claim_token: str, state: str, result: dict[str, Any]
    ) -> None:
        with self.bus.transaction() as db:
            changed = db.execute(
                "UPDATE distribution_deliveries SET state=?,result_json=?,"
                "claim_token=NULL,lease_until=NULL "
                "WHERE delivery_id=? AND claim_token=?",
                (state, canonical_json(result), delivery_id, claim_token),
            ).rowcount
            if not changed:
                raise RuntimeError("distribution_delivery_lease_lost")

    def record_decision(
        self,
        row: dict[str, Any],
        *,
        regex_result: bool,
        jev_result: float | None,
        jev_attempts: int,
        error_code: str | None,
        final_result: str,
    ) -> str:
        decision_id = (
            "decision-"
            + sha256_text(
                canonical_json(
                    [
                        row["article_id"],
                        row["content_version"],
                        row["ticker"],
                        row["terms_revision"],
                        "v1",
                    ]
                )
            )[:32]
        )
        with self.bus.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO distribution_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    row["article_id"],
                    row["content_version"],
                    row["ticker"],
                    row["terms_revision"],
                    "v1",
                    int(regex_result),
                    jev_result,
                    jev_attempts,
                    error_code,
                    final_result,
                    utc_now().isoformat(),
                ),
            )
        return decision_id

    def update_jev_decision(
        self,
        row: dict[str, Any],
        value: float | None,
        attempts: int,
        error_code: str | None,
    ) -> None:
        with self.bus.transaction() as db:
            db.execute(
                "UPDATE distribution_decisions SET jev_result=?,jev_attempts=?,error_code=? "
                "WHERE article_id=? AND content_version=? AND ticker=? "
                "AND terms_revision=? AND classifier_version='v1'",
                (
                    value,
                    attempts,
                    error_code,
                    row["article_id"],
                    row["content_version"],
                    row["ticker"],
                    row["terms_revision"],
                ),
            )

    def pending_for_run(self, run_id: str, ticker: str) -> list[str]:
        with self.bus._connect() as db:
            rows = db.execute(
                "SELECT delivery_id FROM distribution_deliveries WHERE run_id=? AND ticker=? "
                "AND state IN ('WAIT_BODY','PENDING','RUNNING')",
                (run_id, ticker),
            ).fetchall()
        return [row[0] for row in rows]

    def failures_for_run(self, run_id: str, ticker: str) -> list[str]:
        with self.bus._connect() as db:
            rows = db.execute(
                "SELECT delivery_id FROM distribution_deliveries WHERE run_id=? AND ticker=? "
                "AND state IN ('FAILED','CONFIG_INCOMPLETE','EMPTY_CONTENT')",
                (run_id, ticker),
            ).fetchall()
        return [row[0] for row in rows]

    def status(self, source_id: str) -> list[dict[str, Any]]:
        with self.bus._connect() as db:
            rows = db.execute(
                "SELECT run_id,mode,status,coverage,created_at FROM distribution_runs "
                "WHERE source_id=? ORDER BY created_at DESC LIMIT 20",
                (source_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, source_id: str) -> dict[str, Any]:
        with self.bus._connect() as db:
            articles = db.execute(
                "SELECT body_state,COUNT(*) AS count FROM distribution_articles "
                "WHERE source_id=? GROUP BY body_state",
                (source_id,),
            ).fetchall()
            deliveries = db.execute(
                "SELECT d.state,COUNT(*) AS count FROM distribution_deliveries d "
                "JOIN distribution_runs r USING(run_id) WHERE r.source_id=? "
                "GROUP BY d.state",
                (source_id,),
            ).fetchall()
            decisions = db.execute(
                "SELECT COUNT(*) AS total,"
                "SUM(CASE WHEN regex_result=1 THEN 1 ELSE 0 END) AS regex_hits,"
                "SUM(CASE WHEN jev_result>=0.5 THEN 1 ELSE 0 END) AS jev_hits,"
                "SUM(CASE WHEN error_code IS NOT NULL THEN 1 ELSE 0 END) AS jev_errors "
                "FROM distribution_decisions x "
                "JOIN distribution_articles a USING(article_id) WHERE a.source_id=?",
                (source_id,),
            ).fetchone()
        return {
            "source_id": source_id,
            "body_states": {row["body_state"]: row["count"] for row in articles},
            "delivery_states": {row["state"]: row["count"] for row in deliveries},
            "classifier": {
                key: int(decisions[key] or 0)
                for key in ("total", "regex_hits", "jev_hits", "jev_errors")
            },
        }

    def decisions(self, ticker: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.bus._connect() as db:
            rows = db.execute(
                "SELECT * FROM distribution_decisions WHERE ticker=? "
                "ORDER BY created_at DESC LIMIT ?",
                (ticker.upper(), max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]
