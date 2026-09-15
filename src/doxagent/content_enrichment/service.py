"""Queue worker implementing enrichment before Message Bus identity and Raw persistence."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.schema import EnrichmentJob, EnrichmentJobStatus
from doxagent.content_enrichment.transport import DEADLINE
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import ContentEnrichmentMode, RawMessageInput, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.monitoring.media_enrichment import (
    FetchAttempt,
    MediaEnrichmentRecord,
    MediaExtractionResult,
    media_enrichment_metadata,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.repository = repository
        self.bus = bus
        self.concurrency = max(1, min(8, concurrency))
        self.retry_delay_seconds = max(0, retry_delay_seconds)
        self.extractor = extractor or SharedContentExtractor(concurrency=self.concurrency)

    async def run_once(self, *, now: datetime | None = None) -> int:
        current = (now or utc_now()).astimezone(UTC)
        jobs = self.repository.claim_enrichment_jobs(
            limit=self.concurrency, now=current, lease_seconds=60
        )
        if not jobs:
            return 0
        await asyncio.gather(*(self._process_guarded(job, current) for job in jobs))
        return len(jobs)

    async def _process_guarded(self, job: EnrichmentJob, now: datetime) -> None:
        heartbeat = asyncio.create_task(self._renew(job))
        try:
            await self._process(job, now)
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) == "enrichment_lease_lost":
                logger.warning("discarded stale enrichment worker job_id=%s", job.job_id)
                return
            logger.exception(
                "content enrichment infrastructure failure job_id=%s ticker=%s source_id=%s",
                job.job_id,
                job.binding.ticker,
                job.source.source_id,
            )
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _renew(self, job: EnrichmentJob) -> None:
        while job.claim_token:
            await asyncio.sleep(20)
            try:
                self.repository.renew_enrichment_claim(job.job_id, job.claim_token)
            except RuntimeError as exc:
                if str(exc) == "enrichment_lease_lost":
                    return
                raise

    async def run(self, stop: asyncio.Event, *, sleep_seconds: float = 0.25) -> None:
        """Keep free slots occupied without waiting for the slowest member of a batch."""
        active: set[asyncio.Task[None]] = set()
        try:
            while not stop.is_set():
                finished = {task for task in active if task.done()}
                for task in finished:
                    task.result()
                active -= finished
                if len(active) < self.concurrency:
                    now = utc_now()
                    jobs = self.repository.claim_enrichment_jobs(
                        limit=self.concurrency - len(active),
                        now=now,
                        lease_seconds=60,
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
        close = getattr(self.extractor, "close", None)
        if close is not None:
            await close()

    async def _process(self, job: EnrichmentJob, now: datetime) -> None:
        from doxagent.message_bus_v2.admission import evaluate_admission

        reason = evaluate_admission(
            job.message.published_at,
            job.message.admission_context,
            now,
            job.message.publication_time_basis,
        )
        if reason:
            self.repository.record_admission(
                job.message, reason, "ENRICHMENT_DEQUEUE", job.binding.binding_id
            )
            self.repository.delete_enrichment_job(job.job_id, claim_token=job.claim_token)
            return
        generic_url = job.message.metadata.get("identity_evidence", {}).get("url_kind") == "generic"
        if job.source.content_enrichment_mode is ContentEnrichmentMode.SKIP or generic_url:
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
            ticker=job.binding.ticker,
            title=job.message.title,
            body=fallback,
            url=job.message.url,
            raw_url=job.message.url,
            source_name=(
                job.message.publisher_name or job.message.source or job.source.display_name
            ),
        )
        try:
            seconds = (job.deadline_at - max(now, utc_now())).total_seconds()
            if seconds <= 0 or job.attempt_count > 2:
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
                        if isinstance(self.extractor, SharedContentExtractor):
                            result = await self.extractor.extract_version(
                                record, job.pipeline_version
                            )
                        else:
                            result = await self.extractor.extract(record)
                finally:
                    DEADLINE.reset(token)
        except TimeoutError:
            result = MediaExtractionResult(
                record=record,
                reason="deadline_exceeded",
                diagnostics={"outcome": "UNAVAILABLE", "stage": "fetch", "budget_exhausted": True},
            )
        except Exception as exc:
            result = MediaExtractionResult(record=record, reason=type(exc).__name__.lower())

        finished_at = utc_now()
        attempts = [*job.prior_attempts, *(item.to_payload() for item in result.attempts)]
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
                self.repository.requeue_enrichment_job(requeued)
                return

        metadata = media_enrichment_metadata(job.message.metadata, result)
        enrichment = dict(metadata.get("media_enrichment", {}))
        enrichment["attempts"] = attempts
        enrichment["queue_attempt_count"] = job.attempt_count
        enrichment.setdefault("pipeline_version", job.pipeline_version or "legacy")
        enrichment.setdefault("reason_code", result.reason)
        enrichment.setdefault("outcome", "FULL" if result.succeeded else "UNAVAILABLE")
        failure_index = enrichment.get("failure_attempt_id")
        if isinstance(failure_index, int):
            enrichment["failure_attempt_id"] = len(job.prior_attempts) + failure_index
        metadata["media_enrichment"] = enrichment
        metadata["v2_body_completion"] = {
            "attempt_id": job.job_id,
            "started_at": now.isoformat(),
            "completed_at": finished_at.isoformat(),
            "succeeded": result.succeeded,
            "reason": result.reason,
            "attempt_count": job.attempt_count,
        }
        final_url = result.final_url or job.message.url
        publisher = job.message.publisher_name or job.message.source or job.source.display_name
        message = job.message.model_copy(
            update={
                "body": result.content if result.succeeded else job.message.body,
                "source": publisher,
                "publisher_name": publisher,
                "url": final_url,
                "metadata": metadata,
            }
        )
        await self._finalize(job, message)

    async def _finalize(self, job: EnrichmentJob, message: RawMessageInput) -> None:
        await self.bus.accept_message(
            source=job.source,
            binding=job.binding,
            message=message,
            bootstrap=job.bootstrap,
            collected_at=job.created_at,
            trusted_enrichment=True,
            enrichment_input=job.message,
            enrichment_claim=(job.job_id, job.claim_token) if job.claim_token else None,
        )
        self.repository.delete_enrichment_job(job.job_id, claim_token=job.claim_token)

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
            )
        )


__all__ = ["ContentEnrichmentHub"]
