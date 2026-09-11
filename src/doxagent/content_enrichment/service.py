"""Queue worker implementing enrichment before Message Bus identity and Raw persistence."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.schema import EnrichmentJob, EnrichmentJobStatus
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
        try:
            await self._process(job, now)
        except Exception:
            logger.exception(
                "content enrichment infrastructure failure job_id=%s ticker=%s source_id=%s",
                job.job_id,
                job.binding.ticker,
                job.source.source_id,
            )
            raise

    async def close(self) -> None:
        close = getattr(self.extractor, "close", None)
        if close is not None:
            await close()

    async def _process(self, job: EnrichmentJob, now: datetime) -> None:
        if job.source.content_enrichment_mode is ContentEnrichmentMode.SKIP:
            metadata = dict(job.message.metadata)
            metadata["media_enrichment"] = {
                "status": "skipped",
                "succeeded": False,
                "reason": "source_blacklist",
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
            result = await self.extractor.extract(record)
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
        metadata["media_enrichment"] = enrichment
        metadata["v2_body_completion"] = {
            "attempt_id": job.job_id,
            "succeeded": result.succeeded,
            "reason": result.reason,
            "attempt_count": job.attempt_count,
        }
        final_url = result.final_url or job.message.url
        publisher = job.message.publisher_name or job.message.source or job.source.display_name
        message = job.message.model_copy(
            update={
                "body": result.content if result.succeeded else fallback,
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
        )
        self.repository.delete_enrichment_job(job.job_id)

    def _retry_after(self, attempts: Sequence[FetchAttempt]) -> float:
        values = [item.retry_after_seconds for item in attempts if item.retry_after_seconds]
        return min(max(values, default=float(self.retry_delay_seconds)), 180.0)

    @staticmethod
    def _retryable(result: MediaExtractionResult) -> bool:
        if any(item.transient_hint for item in result.attempts):
            return True
        status = result.http_status
        if status in {408, 425, 429} or (status is not None and status >= 500):
            return True
        reason = (result.reason or "").lower()
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
                "captcha",
                "challenge",
            )
        )


__all__ = ["ContentEnrichmentHub"]
