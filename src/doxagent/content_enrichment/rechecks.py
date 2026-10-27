"""Small independent update budget; unchanged lists need no business replay."""

from datetime import timedelta
from doxagent.content_enrichment.schema import EnrichmentJob, EnrichmentJobStatus
from doxagent.message_bus_v2.body_results import ArticleBodyResults
from doxagent.message_bus_v2.schema import utc_now, new_id, sha256_text


def enqueue_due(repository, limit=4):
    now = utc_now()
    with repository._connect() as db:
        if db.execute("SELECT count(*) FROM (SELECT 1 FROM content_enrichment_jobs WHERE status IN ('queued','retry_wait','running') AND json_extract(data_json,'$.work_kind')='recheck' LIMIT 8)").fetchone()[0]>=8:
            return 0
        rows = db.execute(
            "SELECT fetch_key FROM article_body_results WHERE next_check_at<=? "
            "AND coalesce(json_extract(data_json,'$.check_pending'),0)=0 "
            "AND coalesce(json_extract(data_json,'$.eligible'),1)=1 ORDER BY next_check_at LIMIT ?",
            (now.isoformat(), limit),
        ).fetchall()
    count = 0
    results = ArticleBodyResults(repository)
    for row in rows:
        key = row[0]
        # Decode the one template outside the writer; never scan old lists.
        with repository._connect() as db:
            template = db.execute(
                "SELECT data_json FROM content_enrichment_jobs WHERE json_extract(data_json,'$.body_result_key')=? ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
        job = EnrichmentJob.model_validate_json(template[0]) if template else None
        with repository.atomic_intake() as db:
            value = results.get(key)
            if not value or value.get("check_pending") or value["next_check_at"] > now.isoformat():
                continue
            if not job or job.status not in {
                EnrichmentJobStatus.COMPLETED,
                EnrichmentJobStatus.FAILED,
            }:
                value["next_check_at"] = (now + timedelta(minutes=30)).isoformat()
                results._save(db, value)
                continue
            if job.binding:
                binding = repository.get_binding(job.binding.binding_id)
                source = repository.get_source(job.source.source_id)
                ticker = repository.get_ticker_state(job.binding.ticker)
                if (
                    not binding
                    or not source
                    or not binding.enabled
                    or not source.enabled
                    or not ticker
                    or ticker.status.value != "running"
                ):
                    value["next_check_at"] = (now + timedelta(minutes=30)).isoformat()
                    results._save(db, value)
                    continue
                known = repository.known_published_article(binding, job.message)
            else:
                binding = None
                source = repository.get_source(job.source.source_id)
                known = None
            if not source:
                continue
            sequence = value.get("check_seq", 0) + 1
            identity = sha256_text("recheck:" + key + ":" + str(sequence))
            fresh = job.model_copy(
                update={
                    "job_id": new_id("body-check"),
                    "intake_key": identity,
                    "source": source,
                    "binding": binding,
                    "work_kind": "recheck",
                    "bootstrap": False,
                    "status": EnrichmentJobStatus.QUEUED,
                    "attempt_count": 0,
                    "prior_attempts": [],
                    "created_at": now,
                    "updated_at": now,
                    "last_seen_at": now,
                    "not_before": now,
                    "deadline_at": now + timedelta(seconds=job.fetch_budget_seconds),
                    "lease_expires_at": None,
                    "claim_token": None,
                    "execution_seq": 0,
                    "budget_started_at": None,
                    "first_fetch_at": None,
                    "result": {},
                    "known_article_raw_id": known.raw_message_id
                    if known
                    else job.known_article_raw_id,
                }
            )
            repository.enqueue_enrichment_job(fresh)
            value.update(check_pending=True, check_seq=sequence)
            results._save(db, value)
            count += 1
    return count
