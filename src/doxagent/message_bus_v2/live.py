"""Realtime article work and ticker decisions without poll-run relays."""

from __future__ import annotations

import asyncio
import logging
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from doxagent.content_enrichment.schema import EnrichmentJob
from .body_results import ArticleBodyResults
from .live_repository import LiveRepository
from .monitoring_terms import MonitoringTermsService
from .relevance import regex_relevant
from .schema import AcquisitionMode, RawMessageInput, utc_now

log = logging.getLogger(__name__)


class LiveQueue:
    def __init__(self, bus):
        self.bus = bus
        self.repository = LiveRepository(bus.repository)
        self._sources = {}
        self._claim_round = 0
        self._spool_replay_at = 0

    def source(self, identity):
        with self.bus.repository._connect() as db:
            row = db.execute(
                "SELECT version,enabled FROM source_definitions WHERE source_id=?", (identity,)
            ).fetchone()
        if not row or not row[1]:
            return None
        cached = self._sources.get(identity)
        if cached and cached.version == row[0]:
            return cached
        source = self.bus.repository.get_source(identity)
        self._sources[identity] = source
        return source

    async def claim(self, limit, *, allow_background=True):
        if time.monotonic() - self._spool_replay_at >= 1:
            self._spool_replay_at = time.monotonic()
            pending = await self.bus.storage.prepare(self.repository.pending_spool, None)
            for value in pending:
                current = await self.bus.storage.read(self.repository.get, value["key"])
                if (
                    not current
                    or str(current["input_revision"]) != str(value["input_revision"])
                    or current.get("last_result_id") == value["result_id"]
                ):
                    await asyncio.to_thread(Path(value["spool_path"]).unlink, missing_ok=True)
                elif current.get("claim_token") == value["token"]:
                    await self.bus.storage.call(
                        self.repository.commit_result, value, _lane_priority=0
                    )
                    await asyncio.to_thread(Path(value["spool_path"]).unlink, missing_ok=True)
        self._claim_round += 1
        claims = []
        if allow_background and self._claim_round % 8 == 0:
            claims = await self.bus.storage.call(self.repository.claim, limit=1, background=True)
        if len(claims) < limit:
            claims += await self.bus.storage.call(self.repository.claim, limit=limit - len(claims))
        if allow_background and len(claims) < limit:
            claims += await self.bus.storage.call(
                self.repository.claim, limit=limit - len(claims), background=True
            )
        jobs = []
        for key, revision, token in claims:
            # Prefer a previously successful download to any new HTTP work.
            resumed = False
            pending = await self.bus.storage.prepare(self.repository.pending_spool, key)
            for value in pending:
                current = await self.bus.storage.read(self.repository.get, key)
                if current.get("last_result_id") == value["result_id"]:
                    await asyncio.to_thread(Path(value["spool_path"]).unlink, missing_ok=True)
                    continue
                if str(value["input_revision"]) == str(revision):
                    await self.bus.storage.call(
                        self.repository.commit_result, value, token=token, _lane_priority=0
                    )
                    resumed = True
                await asyncio.to_thread(Path(value["spool_path"]).unlink, missing_ok=True)
            if resumed:
                continue
            job = await self.bus.storage.prepare(self._job, key, revision, token)
            if job:
                jobs.append(job)
        return jobs

    def _job(self, key, revision, token):
        row = self.repository.get(key)
        source = self.source(row["source_id"])
        if source is None:
            # Already captured input is allowed to finish after source disable.
            source = self.bus.repository.get_source(row["source_id"])
        if source is None:
            return None
        message = RawMessageInput.model_validate(self.repository.load(row["input_ref"]))
        if row["url"] != message.url:
            metadata = {**message.metadata}
            metadata.pop("canonical_resolution", None)
            message = message.model_copy(update={"url": row["url"], "metadata": metadata})
        with self.bus.repository._connect() as db:
            first = db.execute(
                "SELECT binding_id FROM live_deliveries WHERE article_key=? LIMIT 1", (key,)
            ).fetchone()
        binding = self.bus.repository.get_binding(first[0]) if first else None
        budget = (
            180 if source.adapter_ref in {"builtin:sec_edgar@1", "builtin:defense_public@1"} else 20
        )
        started = datetime.fromisoformat(row["first_fetch_at"])
        attempts = json.loads(row["attempts_json"])
        spent = (
            sum(
                float(a.get("latency_ms", 0))
                for a in attempts
                if a.get("reason")
                not in {
                    "site_budget_deferred",
                    "site_recovery_pending",
                    "storage_unavailable",
                    "domain_cooldown",
                }
            )
            / 1000
        )
        cached = None
        revalidation = None
        if row["result_ref"] and row["body_digest"]:
            prior = RawMessageInput.model_validate(self.repository.load(row["result_ref"]))
            meta = prior.metadata.get("media_enrichment", {})
            revalidation = {
                "content": prior.body,
                "url": prior.url,
                "final_url": prior.url,
                "outcome": meta.get("outcome"),
                "acquired_at": row["acquired_at"],
                "validators": json.loads(row["validators_json"]) or meta.get("validators", {}),
                "diagnostics": meta,
            }
        if row["work_kind"] == "new_article":
            candidate = ArticleBodyResults(self.bus.repository).get(key)
            if not candidate:
                candidate = self.resolved_reuse(message, source.source_id, message.url)
            if not candidate and binding:
                known = self.bus.repository.known_published_article(binding, message)
                old_key = known.metadata.get("article_body_result_key") if known else None
                if old_key:
                    value = ArticleBodyResults(self.bus.repository).get(old_key)
                    if value and ArticleBodyResults.compatible(value, message, source.source_id):
                        candidate = value
            if (
                candidate
                and candidate.get("content")
                and candidate.get("outcome") in {"FULL", "SHORT_FULL"}
                and not (
                    candidate.get("language")
                    and message.metadata.get("language")
                    and candidate["language"] != message.metadata["language"]
                )
            ):
                cached = candidate
        return EnrichmentJob(
            job_id="live:" + key,
            intake_key=key,
            poll_run_id="live",
            source=source,
            binding=binding,
            owner_kind="live_article",
            article_id=key,
            message=message,
            provider_raw_hash=str(revision),
            created_at=datetime.fromisoformat(row["first_seen_at"]),
            deadline_at=utc_now() + timedelta(seconds=max(0.001, budget - spent)),
            fetch_budget_seconds=budget,
            first_fetch_at=started,
            budget_started_at=started,
            claim_token=token,
            status="running",
            attempt_count=row["attempt"],
            execution_seq=row["attempt"],
            work_kind=row["work_kind"],
            pipeline_version="body_v2.2",
            prior_attempts=attempts,
            result={"live_cached": cached}
            if cached
            else {"live_revalidation": revalidation}
            if revalidation
            else {},
        )

    def resolved_reuse(self, message, source_id, url):
        final = message.model_copy(update={"url": url})
        from .body_results import article_key

        key = article_key(final, source_id)
        with self.bus.repository._connect() as db:
            row = db.execute(
                "SELECT a.* FROM live_articles a LEFT JOIN live_origins o ON o.origin_key=? "
                "WHERE a.article_key=coalesce(o.article_key,?) AND a.result_ref IS NOT NULL AND a.outcome IN ('FULL','SHORT_FULL')",
                ("canonical:" + key, key),
            ).fetchone()
        if row:
            body = RawMessageInput.model_validate(self.repository.load(row["result_ref"]))
            return {
                "content": body.body,
                "outcome": row["outcome"],
                "diagnostics": body.metadata.get("media_enrichment", {}),
                "final_url": body.url,
                "url": body.url,
                "acquired_at": row["acquired_at"],
                "execution_id": row["last_result_id"],
            }
        return ArticleBodyResults(self.bus.repository).resolved_reuse(
            final, source_id, url, new_discovery=True
        )

    async def finish(self, job, message, outcome=None):
        prepared = await self.bus.storage.prepare(
            self.repository.prepare_result, job, message, outcome=outcome
        )
        await self.bus.storage.call(self.repository.commit_result, prepared, _lane_priority=0)
        await asyncio.to_thread(Path(prepared["spool_path"]).unlink, missing_ok=True)

    async def checks(self):
        await self.bus.storage.call(self.repository.retire_acceptances, _lane_priority=2)
        return await self.bus.storage.call(self.repository.enqueue_checks, _lane_priority=2)

    async def adopt(self, job):
        """One-time transfer of a frozen old work item, not a second BODY lane."""

        def prepare():
            targets = []
            if job.binding:
                targets = [(job.binding.binding_id, job.binding.ticker, 0)]
            elif job.article_id:
                with self.bus.repository._connect() as db:
                    rows = db.execute(
                        "SELECT binding_json,terms_revision FROM distribution_deliveries WHERE article_id=?",
                        (job.article_id,),
                    ).fetchall()
                targets = list(
                    {
                        (json.loads(row[0])["binding_id"], json.loads(row[0])["ticker"], row[1])
                        for row in rows
                    }
                )
            prepared = self.repository.prepare_input(
                job.source,
                targets,
                [job.message],
                observed_at=job.created_at.isoformat(),
                unit="LEGACY_CONTINUATION",
                bootstrap=job.bootstrap and job.work_kind != "recheck",
                admission=job.message.admission_context,
                recheck=job.work_kind == "recheck",
            )
            if job.work_kind == "recheck":
                previous = (
                    ArticleBodyResults(self.bus.repository).get(job.body_result_key)
                    if job.body_result_key
                    else None
                )
                for item in prepared:
                    item["recheck"] = True
                    item["kind"] = "recheck"
                    if previous and previous.get("first_observed_at"):
                        item["first_seen"] = previous["first_observed_at"]
                        item["legacy_body_key"] = job.body_result_key
            return prepared

        prepared = await self.bus.storage.prepare(prepare)

        def commit():
            with self.bus.repository.atomic_intake() as db:
                self.bus.repository._assert_enrichment_claim(db, job.job_id, job.claim_token)
                keys = self.repository.accept(prepared)
                if not keys:
                    db.execute(
                        "UPDATE content_enrichment_jobs SET status='completed',lease_expires_at=NULL,"
                        "data_json=json_set(data_json,'$.status','completed','$.claim_token',NULL,'$.lease_expires_at',NULL,"
                        "'$.result',json(?)) WHERE job_id=?",
                        (canonical_json({"outcome": "ADMISSION_REJECTED"}), job.job_id),
                    )
                    if job.owner_kind == "distribution_article":
                        db.execute(
                            "UPDATE distribution_deliveries SET state='ADMISSION_REJECTED',claim_token=NULL,lease_until=NULL "
                            "WHERE article_id=? AND state IN ('WAIT_BODY','PENDING','L2_WAIT')",
                            (job.article_id,),
                        )
                    return []
                key = keys[0].removeprefix("live:")
                if job.work_kind == "recheck":
                    db.execute(
                        "UPDATE live_articles SET state='FETCH_DUE',work_kind='recheck',due_at=?,attempt=0,"
                        "first_fetch_at=NULL,attempts_json='[]' WHERE article_key=? AND state IN ('READY','FETCH_DUE') "
                        "AND (checked_at IS NULL OR checked_at<?)",
                        (utc_now().isoformat(), key, job.created_at.isoformat()),
                    )
                pointer = canonical_json(
                    {
                        "continuation_id": "live:"
                        + key
                        + (":" + job.binding.ticker if job.binding else "")
                    }
                )
                db.execute(
                    "UPDATE content_enrichment_jobs SET status='completed',lease_expires_at=NULL,"
                    "data_json=json_set(data_json,'$.status','completed','$.lease_expires_at',NULL,'$.claim_token',NULL,"
                    "'$.result',json(?)) WHERE job_id=?",
                    (pointer, job.job_id),
                )
                if job.owner_kind == "distribution_article":
                    db.execute(
                        "UPDATE distribution_deliveries SET state='DEDUPLICATED',claim_token=NULL,lease_until=NULL,"
                        "result_json=json_object('continuation_id',?||':'||ticker) WHERE article_id=? AND state IN ('WAIT_BODY','PENDING','L2_WAIT')",
                        ("live:" + key, job.article_id),
                    )

        from .schema import canonical_json

        await self.bus.storage.call(commit, _lane_priority=1)

    def background_active(self):
        with self.bus.repository._connect() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM live_articles WHERE state='FETCHING' AND work_kind='recheck' "
                    "AND lease_until>? LIMIT 1",
                    (utc_now().isoformat(),),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM content_enrichment_jobs WHERE status='running' AND lease_expires_at>? "
                    "AND json_extract(data_json,'$.work_kind')='recheck' LIMIT 1",
                    (utc_now().isoformat(),),
                ).fetchone()
            )


class LiveDeliveryWorker:
    """Fast classification/publication is never held behind an L2 roundtrip."""

    def __init__(self, bus, *, jev=None):
        self.bus = bus
        self.queue = bus.live_queue
        self.repository = self.queue.repository
        self.terms = MonitoringTermsService(bus.repository)
        from .distribution import DistributionWorker

        self.classifier = DistributionWorker(None, bus, self.terms, jev=jev)
        self.task = None
        self.decisions = set()
        self.active_articles = set()
        self.configuration_revision = None
        self.configuration_checked_at = 0

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def close(self):
        tasks = [*self.decisions] + ([self.task] if self.task else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _prepare(self, row):
        message = RawMessageInput.model_validate(self.repository.load(row["result_ref"]))
        source = self.queue.source(row["source_id"]) or self.bus.repository.get_source(
            row["source_id"]
        )
        binding = self.bus.repository.get_binding(row["binding_id"])
        if binding and not row.get("raw_id"):
            known = self.bus.repository.known_published_article(binding, message)
            if known:
                row["raw_id"] = known.raw_message_id
        found = self.terms.get(row["ticker"])
        if found:
            row["terms_revision"] = found[0]
        return message, source, binding, found

    async def publish(self, row, source, binding, message):
        prepared = await self.bus.storage.prepare(
            self.repository.prepare_publication, self.bus, row, source, binding, message
        )
        await self.bus.storage.call(self.repository.publish, prepared, _lane_priority=0)

    async def process(self, row):
        decision_terms = row["terms_revision"]
        message, source, binding, found = await self.bus.storage.prepare(self._prepare, row)
        if not source or not binding or not binding.enabled:
            await self.bus.storage.call(self.repository.decision, row, "CANCELLED")
            return
        from .distribution_repository import _complete_body_required, _complete_body_verified

        if _complete_body_required(source) and not _complete_body_verified(message):
            await self.bus.storage.call(self.repository.decision, row, "NO_PUBLIC_BODY")
            return
        if row.get("raw_id") or source.acquisition_mode is not AcquisitionMode.BY_DISTRIBUTION:
            await self.publish(row, source, binding, message)
            return
        if found is None:
            await self.bus.storage.call(
                self.repository.decision, row, "CONFIG_INCOMPLETE", error="TERMS_MISSING"
            )
            return
        revision, definition = found
        if (
            row.get("decision_digest") == row["body_digest"]
            and decision_terms == revision
            and (row.get("regex_result") or (row.get("jev_result") or 0) >= 0.5)
        ):
            await self.publish(row, source, binding, message)
            return
        if regex_relevant(definition, source.content_language or "en", message):
            await self.bus.storage.call(self.repository.decision, row, "PENDING", regex=True)
            await self.publish(row, source, binding, message)
        elif source.distribution_policy and source.distribution_policy.jev_enabled:
            await self.bus.storage.call(self.repository.decision, row, "L2_DUE", regex=False)
        else:
            await self.bus.storage.call(self.repository.decision, row, "IRRELEVANT", regex=False)

    async def classify(self, rows):
        key = rows[0]["article_key"]
        try:
            prepared = [
                (row, *(await self.bus.storage.prepare(self._prepare, row))) for row in rows
            ]
            definitions = {
                row["ticker"]: found[1]
                for row, message, source, binding, found in prepared
                if source and binding and found
            }
            message = prepared[0][1]

            async def positive(ticker, score, attempt):
                for row, msg, source, binding, found in prepared:
                    if row["ticker"] == ticker:
                        if await self.bus.storage.call(
                            self.repository.decision, row, "PENDING", regex=False, score=score
                        ):
                            await self.publish(row, source, binding, msg)

            scores, attempts, errors = await self.classifier._jev(
                message, definitions, on_positive=positive
            )
            for row, msg, source, binding, found in prepared:
                score = scores.get(row["ticker"])
                if score is not None and score >= 0.5:
                    continue
                error = errors.get(row["ticker"])
                permanent = error in {
                    "JEV_INPUT_LIMIT",
                    "JEV_DISABLED",
                    "JEV_HTTP_400",
                    "JEV_HTTP_401",
                    "JEV_HTTP_403",
                    "JEV_HTTP_404",
                    "JEV_HTTP_422",
                }
                await self.bus.storage.call(
                    self.repository.decision,
                    row,
                    "CONFIG_INCOMPLETE"
                    if permanent
                    else "L2_DUE"
                    if row["ticker"] in errors
                    else "IRRELEVANT",
                    regex=False,
                    score=score,
                    error=error,
                    retry=row["ticker"] in errors,
                )
        except Exception as exc:
            for row in rows:
                await self.bus.storage.call(
                    self.repository.decision, row, "L2_DUE", error=type(exc).__name__, retry=True
                )
            raise
        finally:
            self.active_articles.discard(key)

    def _configuration_revision(self):
        with self.bus.repository._connect() as db:
            return db.execute("SELECT revision FROM bus_configuration WHERE id=1").fetchone()[0]

    async def run_available(self):
        if time.monotonic() - self.configuration_checked_at >= 1:
            revision = await self.bus.storage.read(self._configuration_revision)
            if revision != self.configuration_revision:
                await self.bus.storage.call(
                    self.repository.refresh_decisions,
                    restarted=self.configuration_revision is None,
                    _lane_priority=1,
                )
                self.configuration_revision = revision
            self.configuration_checked_at = time.monotonic()
        for task in tuple(self.decisions):
            if task.done():
                self.decisions.remove(task)
                if not task.cancelled() and task.exception():
                    log.error("live L2 continuation failed: %s", type(task.exception()).__name__)
        rows = await self.bus.storage.read(self.repository.deliveries)
        for row in rows:
            try:
                await self.process(row)
            except Exception as exc:
                log.warning("live delivery awaiting local recovery: %s", type(exc).__name__)
                await self.bus.storage.call(
                    self.repository.decision, row, "PENDING", error=type(exc).__name__, retry=True
                )
        if len(self.decisions) < 2:
            pending = await self.bus.storage.read(self.repository.deliveries, "L2_DUE")
            groups = defaultdict(list)
            for row in pending:
                if row["article_key"] not in self.active_articles:
                    groups[row["article_key"]].append(row)
            for key, group in list(groups.items())[: 2 - len(self.decisions)]:
                self.active_articles.add(key)
                self.decisions.add(asyncio.create_task(self.classify(group)))
        return len(rows)

    async def run(self):
        while True:
            try:
                await self.run_available()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("live delivery pump awaiting local recovery")
            await asyncio.sleep(0.1)
