"""Queue worker implementing enrichment before Message Bus identity and Raw persistence."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.native import native_article
from doxagent.content_enrichment.schema import EnrichmentJob, EnrichmentJobStatus
from doxagent.content_enrichment.transport import (
    DEADLINE,
    REVALIDATION,
    RESUME_STAGE,
    WORK_AGE,
    WORK_CLASS,
)
from doxagent.message_bus_v2.pressure import background_allowed
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import ContentEnrichmentMode, RawMessageInput, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.storage_lane import WriteAdmissionBusy
from doxagent.monitoring.media_enrichment import (
    FetchAttempt,
    MediaEnrichmentRecord,
    MediaExtractionResult,
    media_enrichment_metadata,
)
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import BodyOutcome
from doxagent.site_strategy.scope import source_routed

logger = logging.getLogger(__name__)


def _storage_busy(exc):
    return isinstance(exc,WriteAdmissionBusy) or isinstance(exc,sqlite3.OperationalError) and (
        (getattr(exc,'sqlite_errorcode',0)&255) in {5,6}
        or str(exc).lower() in {'database is locked','database table is locked','database is busy'})


class ExtractorLike(Protocol):
    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult: ...


class ContentEnrichmentHub:
    """One process-wide worker shared by every source and ticker."""

    def __init__(
        self,
        repository: MessageBusV2Repository,
        bus: MessageBusV2Service,
        *,
        extractor: ExtractorLike | None = None,
        concurrency: int = 8,
        retry_delay_seconds: int = 30,
        site_access_client: SiteAccessClient | None = None,
    ) -> None:
        self.repository = repository
        self.bus = bus
        self.concurrency = max(1, min(8, concurrency))
        self.retry_delay_seconds = max(0, retry_delay_seconds)
        self.extractor = extractor or SharedContentExtractor(concurrency=self.concurrency)
        self.site_access_client = site_access_client
        self._last_outbox_flush = 0.0
        self.ir_body_reader = None
        self.defense_body_reader = None
        self.sec_body_reader = None
        self.technology_body_reader = None
        self.energy_body_reader = None
        self._google_slots = asyncio.Semaphore(4)
        self._google_client = None
        self._google_flights = {}
        from doxagent.message_bus_v2.distribution_repository import DistributionRepository

        self.distribution = DistributionRepository(repository)

    async def run_once(self, *, now: datetime | None = None) -> int:
        await self._flush_outcomes(force=True)
        current = (now or utc_now()).astimezone(UTC)
        jobs = await self.bus.storage.call(
            self.repository.claim_enrichment_jobs,
            limit=self.concurrency,
            now=current,
            lease_seconds=60,
            allow_rechecks=background_allowed(),
        )
        if not jobs:
            return 0
        await asyncio.gather(*(self._process_guarded(job, current) for job in jobs))
        await self._flush_outcomes(force=True)
        return len(jobs)

    async def _process_guarded(self, job: EnrichmentJob, now: datetime) -> None:
        work_token = WORK_CLASS.set("recheck" if job.work_kind == "recheck" else "backfill" if job.bootstrap else "foreground")
        age_token = WORK_AGE.set(max(0, (utc_now() - job.created_at).total_seconds()))
        heartbeat = asyncio.create_task(self._renew(job))
        processing = asyncio.create_task(self._process(job,now))
        try:
            await asyncio.wait({processing,heartbeat},return_when=asyncio.FIRST_COMPLETED)
            if heartbeat.done() and not heartbeat.cancelled() and heartbeat.exception():
                raise heartbeat.exception()
            await processing
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) == "enrichment_lease_lost":
                logger.warning("discarded stale enrichment worker job_id=%s", job.job_id)
                return
            if _storage_busy(exc):
                logger.warning('enrichment job deferred for storage contention job_id=%s',job.job_id)
                return  # Durable claim expires; no stale result or broad worker restart.
            logger.exception(
                "content enrichment infrastructure failure job_id=%s ticker=%s source_id=%s",
                job.job_id,
                job.binding.ticker if job.binding else None,
                job.source.source_id,
            )
            raise
        finally:
            WORK_CLASS.reset(work_token)
            WORK_AGE.reset(age_token)
            heartbeat.cancel()
            processing.cancel()
            await asyncio.gather(heartbeat,processing,return_exceptions=True)
            if job.body_result_key:
                from doxagent.message_bus_v2.body_results import ArticleBodyResults

                try:
                    await self.bus.storage.call(
                        ArticleBodyResults(self.repository).release, job.body_result_key, job.execution_id)
                except (sqlite3.OperationalError,WriteAdmissionBusy) as exc:
                    if not _storage_busy(exc):
                        raise
                    logger.warning('body result release deferred job_id=%s',job.job_id)

    async def _renew(self, job: EnrichmentJob) -> None:
        while job.claim_token:
            await asyncio.sleep(20)
            for attempt in range(3):
                try:
                    async with asyncio.timeout(10):
                        await self.bus.storage.call(self.repository.renew_enrichment_claim,job.job_id,job.claim_token)
                    break
                except TimeoutError as exc:
                    raise RuntimeError('enrichment_lease_lost') from exc
                except (sqlite3.OperationalError,WriteAdmissionBusy) as exc:
                    if not _storage_busy(exc):
                        raise
                    if attempt==2:
                        raise RuntimeError('enrichment_lease_lost') from exc
                    await asyncio.sleep((.5,1)[attempt])

    async def run(self, stop: asyncio.Event, *, sleep_seconds: float = 0.25) -> None:
        """Keep free slots occupied without waiting for the slowest member of a batch."""
        active: set[asyncio.Task[None]] = set()
        try:
            while not stop.is_set():
                await self._flush_outcomes()
                finished = {task for task in active if task.done()}
                for task in finished:
                    task.result()
                active -= finished
                if len(active) < self.concurrency:
                    now = utc_now()
                    jobs = await self.bus.storage.call(
                        self.repository.claim_enrichment_jobs,
                        limit=self.concurrency - len(active),
                        now=now,
                        lease_seconds=60,
                        allow_rechecks=background_allowed(),
                    )
                    active.update(
                        asyncio.create_task(self._process_guarded(job, now)) for job in jobs
                    )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=sleep_seconds)
                except TimeoutError:
                    pass
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)

    async def close(self) -> None:
        if self._google_client is not None:
            await self._google_client.aclose()
        for task in self._google_flights.values():
            task.cancel()
        await asyncio.gather(*self._google_flights.values(), return_exceptions=True)
        if self.sec_body_reader is not None:
            await self.sec_body_reader.close()
        if self.energy_body_reader is not None:
            await self.energy_body_reader.close()
        if self.technology_body_reader is not None:
            await self.technology_body_reader.close()
        if self.defense_body_reader is not None:
            await self.defense_body_reader.close()
        if self.ir_body_reader is not None:
            await self.ir_body_reader.close()
        close = getattr(self.extractor, "close", None)
        if close is not None:
            await close()

    async def _google_url(self, wrapper):
        """Resolve only accepted candidates; a shared durable mapping survives restarts."""
        cached = await self.bus.storage.read(self.repository.polling.google_url, wrapper)
        if cached:
            return cached
        task = self._google_flights.get(wrapper)
        if task is None:

            async def resolve():
                import httpx
                from doxagent.message_bus_v2.google_news import resolve_google_news_urls

                if self._google_client is None:
                    self._google_client = httpx.AsyncClient(timeout=8)

                @asynccontextmanager
                async def permit():
                    yield

                async with self._google_slots, asyncio.timeout(20):
                    values = await resolve_google_news_urls(self._google_client, [wrapper], permit)
                target = values.get(wrapper)
                if not target or target == wrapper:
                    raise ValueError("google_url_resolution_failed")
                return await self.bus.storage.call(
                    self.repository.polling.google_url, wrapper, target
                )

            task = asyncio.create_task(resolve())
            self._google_flights[wrapper] = task
            task.add_done_callback(lambda t: self._google_flights.pop(wrapper, None))
        return await asyncio.shield(task)

    @source_routed
    async def _process(self, job: EnrichmentJob, now: datetime) -> None:
        from doxagent.message_bus_v2.admission import evaluate_admission

        reason = (
            None
            if job.owner_kind == "distribution_article"
            else evaluate_admission(
                job.message.published_at,
                job.message.admission_context,
                now,
                job.message.publication_time_basis,
            )
        )
        if reason and job.binding and not job.known_article_raw_id:
            await self.bus.storage.call(
                self.repository.record_admission,
                job.message,
                reason,
                "ENRICHMENT_DEQUEUE",
                job.binding.binding_id,
            )
            await self.bus.storage.call(
                self.repository.delete_enrichment_job, job.job_id, claim_token=job.claim_token
            )
            return
        generic_url = job.message.metadata.get("identity_evidence", {}).get("url_kind") == "generic"
        native = native_article(job.message)
        native_event = (
            job.source.adapter_ref == "builtin:energy_public@1"
            and job.source.source_id == "nrc_event_notifications"
        )
        if job.source.content_enrichment_mode is ContentEnrichmentMode.SKIP or (
            generic_url and not native and not native_event
        ):
            metadata = dict(job.message.metadata)
            metadata["media_enrichment"] = {
                "status": "skipped",
                "succeeded": False,
                "reason": "non_article_url" if generic_url else "source_blacklist",
                "attempted_at": now.isoformat(),
                "attempts": [],
            }
            await self._finalize(job, job.message.model_copy(update={"metadata": metadata}))
            return

        fallback = job.message.fallback_body
        record = MediaEnrichmentRecord(
            standard_message_id=job.job_id,
            raw_message_id=job.job_id,
            source_id=job.source.source_id,
            ticker=job.binding.ticker if job.binding else None,
            title=job.message.title,
            body=fallback,
            url=job.message.url,
            raw_url=job.message.url,
            source_name=(
                job.message.publisher_name or job.message.source or job.source.display_name
            ),
        )
        from doxagent.message_bus_v2.body_results import ArticleBodyResults

        body_results = ArticleBodyResults(self.repository)
        cached = None
        reused = False
        if job.body_result_key:
            cached, reused, acquired = await self.bus.storage.call(
                body_results.prepare, job.body_result_key, job.execution_id, job.message
            )
            if not acquired and not reused:
                await self.bus.storage.call(self.repository.requeue_enrichment_job, job.model_copy(update={
                    "status": EnrichmentJobStatus.RETRY_WAIT,
                    "attempt_count": max(0, job.attempt_count - 1),
                    "not_before": utc_now() + timedelta(seconds=1), "lease_expires_at": None,
                }))
                return
        revalidation_token = REVALIDATION.set(cached if not reused else None)
        continuation = next(
            (
                a.get("phase")
                for a in reversed(job.prior_attempts)
                if a.get("reason") == "site_budget_deferred"
            ),
            None,
        )
        continuation_token = RESUME_STAGE.set(continuation)
        try:
            seconds = (job.deadline_at - max(now, utc_now())).total_seconds()
            queue_expired = job.budget_started_at is None and utc_now()-job.created_at > timedelta(minutes=10)
            if not reused and job.claim_token and not queue_expired:
                job = await self.bus.storage.call(self.repository.start_enrichment_fetch,job)
                seconds = (job.deadline_at-utc_now()).total_seconds()
            if queue_expired:
                seconds = 0
            if reused:
                result = MediaExtractionResult(
                    record=record, content=cached["content"], final_url=cached.get("final_url"),
                    extraction_method="article_body_cache",
                    diagnostics={**cached.get("diagnostics", {}), "site_access_trace": [],
                                 "reused_from_execution_id": cached.get("execution_id"),
                                 "body_acquired_at": cached.get("acquired_at")},
                )
            elif job.source.source_id == "corporate_ir_press_releases" and seconds > 0:
                from doxagent.message_bus_v2.corporate_ir.body import IrBodyReader

                if self.ir_body_reader is None:
                    self.ir_body_reader = IrBodyReader()
                async with asyncio.timeout(seconds):
                    result = await self.ir_body_reader.extract(job,record)
            elif job.source.adapter_ref == "builtin:defense_public@1" and seconds > 0:
                from doxagent.message_bus_v2.defense_body import DefenseBodyReader

                if self.defense_body_reader is None:
                    self.defense_body_reader = DefenseBodyReader()
                async with asyncio.timeout(seconds):
                    result = await self.defense_body_reader.extract(job, record)
            elif job.source.adapter_ref == "builtin:sec_edgar@1" and seconds > 0:
                from doxagent.message_bus_v2.sec_edgar.body import SecBodyReader

                if self.sec_body_reader is None:
                    self.sec_body_reader = SecBodyReader()
                async with asyncio.timeout(seconds):
                    result = await self.sec_body_reader.extract(job, record)
            elif job.source.adapter_ref == "builtin:energy_public@1" and seconds > 0:
                from doxagent.message_bus_v2.energy_body import EnergyBodyReader
                from doxagent.message_bus_v2.technology_body import TechnologyBodyReader

                if self.technology_body_reader is None:
                    self.technology_body_reader = TechnologyBodyReader()
                if self.energy_body_reader is None:
                    self.energy_body_reader = EnergyBodyReader(self.technology_body_reader.http)
                async with asyncio.timeout(seconds):
                    result = await self.energy_body_reader.extract(job, record)
            elif job.source.adapter_ref in {"builtin:technology_public@1", "builtin:thefly_public@1"} and seconds > 0:
                from doxagent.message_bus_v2.technology_body import TechnologyBodyReader

                if self.technology_body_reader is None:
                    self.technology_body_reader = TechnologyBodyReader()
                async with asyncio.timeout(seconds):
                    result = await self.technology_body_reader.extract(job, record)
            elif native:
                result = MediaExtractionResult(
                    record=record,
                    content=native.text,
                    final_url=record.url,
                    extraction_method=native.method,
                    diagnostics={
                        "outcome": "FULL" if len(native.text) >= 800 else "SHORT_FULL",
                        "stage": "validate",
                        "body_source": "provider_article_api",
                        "identity_match": "provider_article_id",
                        "candidate_summary": [native.summary()],
                    },
                )
            elif seconds <= 0 or job.attempt_count > 2:
                result = MediaExtractionResult(
                    record=record,
                    reason="deadline_exceeded" if seconds <= 0 else "attempt_limit_exceeded",
                    diagnostics={
                        "outcome": "UNAVAILABLE",
                        "stage": "intake",
                        "budget_exhausted": True,
                    },
                )
            else:
                token = DEADLINE.set(time.monotonic() + seconds)
                try:
                    async with asyncio.timeout(seconds):
                        if job.message.metadata.get("canonical_resolution") == "pending_body":
                            from urllib.parse import urlsplit

                            target = await self._google_url(job.message.url)
                            domains = job.message.metadata.get("allowed_publisher_domains", [])
                            host = (urlsplit(target).hostname or "").removeprefix("www.").lower()
                            if domains and not any(
                                host == d or host.endswith("." + d) for d in domains
                            ):
                                raise ValueError("domain_constraint_unresolved")
                            record = replace(record, url=target)
                            alias_body = await self.bus.storage.read(body_results.resolved_reuse, job.message, job.source.source_id, target)
                        else:
                            alias_body = None
                        if alias_body:
                            result = MediaExtractionResult(record=record, content=alias_body['content'],
                                final_url=target, extraction_method='resolved_article_cache',
                                diagnostics={**alias_body.get('diagnostics', {}), 'site_access_trace': [],
                                    'reused_from_execution_id': alias_body.get('execution_id'),
                                    'body_acquired_at': alias_body.get('acquired_at')})
                        elif isinstance(self.extractor, SharedContentExtractor):
                            result = await self.extractor.extract_version(
                                record, job.pipeline_version
                            )
                        else:
                            result = await self.extractor.extract(record)
                finally:
                    DEADLINE.reset(token)
        except TimeoutError:
            transport_timeout = (
                job.source.adapter_ref
                in {
                    "builtin:defense_public@1",
                    "builtin:sec_edgar@1",
                    "builtin:technology_public@1",
                    "builtin:energy_public@1",
                }
                and utc_now() < job.deadline_at
            )
            result = MediaExtractionResult(
                record=record,
                reason="transport_timeout" if transport_timeout else "deadline_exceeded",
                diagnostics={"outcome": "UNAVAILABLE", "stage": "fetch",
                             "budget_exhausted": not transport_timeout},
            )
        except Exception as exc:
            logger.exception("body extraction failed job_id=%s source_id=%s", job.job_id, job.source.source_id)
            result = MediaExtractionResult(
                record=record,
                reason=(
                    str(exc)
                    if str(exc) in {"google_url_resolution_failed", "domain_constraint_unresolved"}
                    else type(exc).__name__.lower()
                ),
            )
        finally:
            REVALIDATION.reset(revalidation_token)
            RESUME_STAGE.reset(continuation_token)

        finished_at = utc_now()
        await self.bus.storage.call(self.repository.record_enrichment_execution,
            job, result, started_at=now, reused=reused)
        attempts = [*job.prior_attempts, *(item.to_payload() for item in result.attempts)]
        if not result.succeeded and result.reason in {"storage_unavailable", "site_recovery_pending", "site_budget_deferred"}:
            delay = max((item.retry_after_seconds or 0 for item in result.attempts), default=60)
            not_before = finished_at + timedelta(seconds=max(1, delay) if result.reason == "site_recovery_pending" else 1 if result.reason == "site_budget_deferred" else 60)
            if not_before < job.deadline_at:
                await self.bus.storage.call(
                    self.repository.requeue_enrichment_job,
                    job.model_copy(
                        update={
                            "status": EnrichmentJobStatus.RETRY_WAIT,
                            "attempt_count": max(0, job.attempt_count - 1),
                            "prior_attempts": attempts,
                            "not_before": not_before,
                            "lease_expires_at": None,
                            "updated_at": finished_at,
                        }
                    ),
                )
                return
        if (
            not result.succeeded
            and job.attempt_count == 1
            and self._retryable(result)
            and finished_at < job.deadline_at
        ):
            retry_after = self._retry_after(result.attempts)
            not_before = min(
                finished_at + timedelta(seconds=retry_after),
                job.deadline_at,
            )
            if not_before < job.deadline_at:
                requeued = job.model_copy(
                    update={
                        "status": EnrichmentJobStatus.RETRY_WAIT,
                        "prior_attempts": attempts,
                        "not_before": not_before,
                        "lease_expires_at": None,
                        "updated_at": finished_at,
                    }
                )
                await self.bus.storage.call(self.repository.requeue_enrichment_job, requeued)
                return

        if job.body_result_key and not reused:
            stored_result = await self.bus.storage.call(
                body_results.finish, job.body_result_key, job.execution_id, job.message, result
            )
            if result.succeeded and stored_result and stored_result.get("acquired_at"):
                result.diagnostics["body_acquired_at"] = stored_result["acquired_at"]
        metadata = media_enrichment_metadata(job.message.metadata, result)
        enrichment = dict(metadata.get("media_enrichment", {}))
        enrichment["attempts"] = attempts
        enrichment["queue_attempt_count"] = job.attempt_count
        enrichment.setdefault("pipeline_version", job.pipeline_version or "legacy")
        enrichment.setdefault("reason_code", result.reason)
        enrichment.setdefault("outcome", "FULL" if result.succeeded else "UNAVAILABLE")
        if result.succeeded:
            enrichment.setdefault("body_acquired_at",finished_at.isoformat())
        failure_index = enrichment.get("failure_attempt_id")
        if isinstance(failure_index, int):
            enrichment["failure_attempt_id"] = len(job.prior_attempts) + failure_index
        metadata["media_enrichment"] = enrichment
        metadata["v2_body_completion"] = {
            "attempt_id": job.execution_id,
            "execution_id": job.execution_id,
            "logical_job_id": job.job_id,
            "network_attempted": not reused and any(
                a.reason not in {"site_budget_deferred", "site_recovery_pending", "storage_unavailable", "domain_cooldown"}
                for a in result.attempts
            ),
            "reused_from_execution_id": cached.get("execution_id") if reused else None,
            "started_at": now.isoformat(),
            "completed_at": finished_at.isoformat(),
            "succeeded": result.succeeded,
            "reason": result.reason,
            "attempt_count": job.attempt_count,
        }
        final_url = result.final_url or job.message.url
        publisher = job.message.publisher_name or job.message.source or job.source.display_name
        publication_updates: dict[str, object] = {}
        stamp = result.diagnostics.get("publisher_published_at")
        public_defense = job.source.adapter_ref in {
            "builtin:defense_public@1",
            "builtin:technology_public@1",
            "builtin:energy_public@1",
        }
        if (
            (job.source.source_id in {
                "investorshub_ticker_news", "investing_ticker_news", "corporate_ir_press_releases"
            } or public_defense)
            and (
                job.message.publication_time_basis == "DATE"
                or (public_defense and job.message.publication_time_basis == "UNKNOWN_FIRST_SEEN")
                or job.source.source_id == "investing_ticker_news"
            )
            and result.succeeded
            and isinstance(stamp, str)
        ):
            try:
                exact = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if exact.tzinfo is not None:
                    publication_updates = {
                        "published_at": exact.astimezone(UTC),
                        "publication_time_basis": "EXACT",
                    }
            except ValueError:
                pass
        message = job.message.model_copy(
            update={
                **({"title": result.diagnostics["generated_title"]}
                   if result.succeeded and job.source.adapter_ref == "builtin:sec_edgar@1"
                   and result.diagnostics.get("generated_title") else {}),
                "body": result.content if result.succeeded else job.message.body,
                **({"title": result.diagnostics["publisher_title"]}
                   if result.succeeded and job.source.adapter_ref == "builtin:technology_public@1"
                   and result.diagnostics.get("publisher_title") else {}),
                "source": publisher,
                "publisher_name": publisher,
                "url": final_url,
                "metadata": metadata,
                **publication_updates,
            }
        )
        await self._finalize(job, message)

    async def _finalize(self, job: EnrichmentJob, message: RawMessageInput) -> None:
        from doxagent.source_maintenance.signals import body

        if (
            (
                job.source.adapter_ref in {"builtin:sec_edgar@1", "builtin:thefly_public@1"}
                or job.source.source_id == "nrc_event_notifications"
            )
            and not message.metadata.get("media_enrichment", {}).get("succeeded")
        ):
            # Filing snippets, Fly previews and incomplete NRC events cannot replace full
            # economic content. Keep the failed result/receipt; a later observation can
            # retry through BodyResults without publishing incomplete news.
            await self.bus.storage.call(
                self.repository.complete_enrichment_job_with_outcome,
                job.job_id, job.claim_token, self._outcome_payload(job, message),
            )
            await self.bus.storage.call(body, job, message)
            return
        if job.owner_kind == "distribution_article":
            def finalize():
                self.distribution.finalize_article(
                    job, message, self._outcome_payload(job, message)
                )
                body(job, message)

            await self.bus.storage.call(finalize, _lane_priority=0 if job.work_kind != 'recheck' and not job.bootstrap else 1)
            return
        assert job.binding is not None

        def finalize_binding():
            with self.repository.atomic_intake():
                accepted = self.bus._accept_message(
                    source=job.source,
                    binding=job.binding,
                    message=message,
                    bootstrap=job.bootstrap,
                    collected_at=job.created_at,
                    trusted_enrichment=True,
                    enrichment_input=job.message,
                    enrichment_claim=(job.job_id, job.claim_token) if job.claim_token else None,
                    known_article_raw_id=job.known_article_raw_id,
                )
                self.repository.complete_enrichment_job_with_outcome(
                    job.job_id, job.claim_token, self._outcome_payload(job, message))
            return accepted

        try:
            accepted = await self.bus.storage.call(finalize_binding,
                _lane_priority=0 if job.work_kind != 'recheck' and not job.bootstrap else 1)
        except RuntimeError as exc:
            if not job.known_article_raw_id or str(exc) not in {
                "known_article_revision_identity_changed", "known_article_revision_not_authorized"
            }:
                raise
            await self.bus.storage.call(self.repository.reject_known_article_update,job,str(exc))
            logger.warning("article update rejected job_id=%s reason=%s",job.job_id,str(exc))
            return
        await self.bus.record_consumable(accepted, message)
        await self.bus.storage.call(body, job, message)

    @staticmethod
    def _outcome_payload(job: EnrichmentJob, message: RawMessageInput) -> dict[str, object] | None:
        enrichment = message.metadata.get("media_enrichment", {})
        trace = enrichment.get("site_access_trace") or []
        final_access = trace[-1] if isinstance(trace, list) and trace else {}
        return {
            "job_id": job.job_id,
            "execution_id": job.execution_id,
            "logical_job_id": job.job_id,
            "final_site_id": enrichment.get("site_id") or final_access.get("site_id") or "generic",
            "strategy_ref": enrichment.get("strategy_ref")
            or final_access.get("strategy_ref")
            or "builtin:generic@1",
            "combination_id": final_access.get("combination_id"),
            "outcome": enrichment.get("outcome") or "UNAVAILABLE",
            "reason": enrichment.get("reason_code"),
            "completed_at": utc_now().isoformat(),
            "payload": {
                "cache_reused": bool(enrichment.get("reused_from_execution_id")),
                "site_outcome": enrichment.get("pipeline_version") == "body_v2.2" and message.metadata.get("v2_body_completion",{}).get("network_attempted",True),
                "source_id": job.source.source_id,
                "ticker": job.binding.ticker if job.binding else None,
                "article_id": job.article_id,
                "url": message.url,
                "access_trace": trace,
            },
        }

    async def _flush_outcomes(self, *, force: bool = False) -> None:
        if self.site_access_client is None:
            return
        now = time.monotonic()
        if not force and now - self._last_outbox_flush < 10:
            return
        self._last_outbox_flush = now
        rows = await self.bus.storage.call(self.repository.list_site_strategy_outbox, limit=100)
        if not rows:
            return
        ids = [str(row["outbox_id"]) for row in rows]
        try:
            outcomes = [BodyOutcome.model_validate(row["payload"]) for row in rows]
            await self.site_access_client.submit_outcomes(outcomes)
        except Exception:
            await self.bus.storage.call(self.repository.retry_site_strategy_outbox, ids)
            logger.warning("Site Strategy outcome flush failed", exc_info=True)
        else:
            await self.bus.storage.call(self.repository.confirm_site_strategy_outbox, ids)
            await self.bus.storage.call(self.repository.prune_site_strategy_outbox)

    def _retry_after(self, attempts: Sequence[FetchAttempt]) -> float:
        values = [item.retry_after_seconds for item in attempts if item.retry_after_seconds]
        return min(max(values, default=float(self.retry_delay_seconds)), 180.0)

    @staticmethod
    def _retryable(result: MediaExtractionResult) -> bool:
        reason = (result.reason or "").lower()
        if reason in {
            "deadline_exceeded",
            "subscription_required",
            "login_required",
            "reauth_required",
            "entitlement_missing",
            "challenge_required",
            "non_article_target",
            "publisher_identity_mismatch",
        }:
            return False
        # Preserve the already deployed finite retry for budget deferrals.
        if reason in {"site_budget_deferred", "site_recovery_pending"}:
            return True
        status = result.http_status
        if status is None and result.attempts and result.attempts[-1].reason == result.reason:
            status = result.attempts[-1].status_code
        if status in {408, 425, 429} or (status is not None and status >= 500):
            return True
        return any(
            marker in reason
            for marker in (
                "timeout",
                "connect",
                "connection",
                "network",
                "resolve",
                "reset",
                "refused",
                "transport",
                "ssl",
                "temporarily",
                "rate_limit",
                "domain_cooldown",
                "google_url_resolution_failed",
            )
        )


__all__ = ["ContentEnrichmentHub"]
