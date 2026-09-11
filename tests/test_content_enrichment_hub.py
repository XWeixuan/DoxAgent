from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import (
    ContentEnrichmentMode,
    PollResult,
    RawMessageInput,
    SourceDefinition,
    new_id,
)
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.monitoring.media_enrichment import (
    FetchAttempt,
    MediaEnrichmentRecord,
    MediaExtractionResult,
)


def _setup(tmp_path: Path):
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repository, enrichment_queue_enabled=True)
    bus.bootstrap()
    bus.start_ticker("MU")
    source = bus.require_source("finnhub_company_news")
    binding = repository.get_binding("MU:finnhub_company_news")
    assert binding is not None
    return repository, bus, source, binding


def _message(index: int, *, body: str | None = None, summary: str | None = None):
    return RawMessageInput(
        external_id=str(index),
        title=f"Title {index}",
        body=body,
        summary=summary,
        source="Reuters",
        publisher_name="Reuters",
        url=f"https://example.test/article/{index}",
        published_at=datetime(2026, 9, 11, 10, index % 60, tzinfo=UTC),
        raw_payload={"id": index},
    )


def test_raw_input_allows_empty_body_and_summary() -> None:
    assert _message(59).fallback_body == ""


def test_legacy_short_source_without_mode_defaults_to_blacklist() -> None:
    source = SourceDefinition.model_validate(
        {
            "source_id": "stocktwits_messages",
            "display_name": "Stocktwits",
            "kind": "api",
            "adapter_ref": "builtin:stocktwits_messages",
            "scheduler_group": "stocktwits_messages",
        }
    )
    assert source.content_enrichment_mode is ContentEnrichmentMode.SKIP


class _ConcurrentExtractor:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return MediaExtractionResult(
            record=record,
            content=f"Full article for {record.title}",
            final_url=record.url,
            source_name="example.test",
        )


class _VolatileExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        self.calls += 1
        return MediaExtractionResult(
            record=record,
            content=f"Article body with live quote {self.calls}%",
            final_url=record.url,
            source_name="example.test",
        )


async def test_completed_bootstrap_payload_is_not_reenriched_or_published(
    tmp_path: Path,
) -> None:
    repository, bus, source, binding = _setup(tmp_path)
    extractor = _VolatileExtractor()
    hub = ContentEnrichmentHub(repository, bus, extractor=extractor)

    first = await bus.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(messages=[_message(60, summary="provider summary")]),
    )
    assert first.queued_count == 1
    assert await hub.run_once() == 1
    assert repository.latest_stream_offset("MU") == 0

    repeated = await bus.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(messages=[_message(60, summary="provider summary")]),
    )

    assert repeated.queued_count == 0
    assert repository.list_enrichment_jobs() == []
    assert len(repository.list_raw(ticker="MU")) == 1
    assert repository.latest_stream_offset("MU") == 0
    assert extractor.calls == 1


async def test_global_queue_limits_concurrency_and_keeps_overflow(tmp_path: Path) -> None:
    repository, bus, source, binding = _setup(tmp_path)
    poll = await bus.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(
            messages=[_message(index, summary="provider summary") for index in range(10)]
        ),
    )
    assert poll.queued_count == 10
    assert repository.list_raw(ticker="MU") == []
    extractor = _ConcurrentExtractor()
    hub = ContentEnrichmentHub(repository, bus, extractor=extractor, concurrency=8)

    assert await hub.run_once() == 8
    assert len(repository.list_enrichment_jobs()) == 2
    assert extractor.max_active == 8
    assert await hub.run_once() == 2
    assert repository.list_enrichment_jobs() == []
    assert len(repository.list_raw(ticker="MU")) == 10


class _RetryExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        self.calls += 1
        if self.calls == 1:
            attempt = FetchAttempt(
                phase="direct",
                url=record.url or "",
                status_code=429,
                reason="http_429",
                transient_hint=True,
            )
            return MediaExtractionResult(
                record=record,
                reason="http_429",
                http_status=429,
                attempts=(attempt,),
            )
        return MediaExtractionResult(
            record=record,
            content="Recovered full article body",
            final_url="https://publisher.test/final",
            source_name="publisher.test",
        )


async def test_retry_once_then_identity_and_raw_use_enriched_body(tmp_path: Path) -> None:
    repository, bus, source, binding = _setup(tmp_path)
    bus.enqueue_enrichment(
        source=source,
        binding=binding,
        message=_message(1, summary="provider summary"),
        bootstrap=False,
        poll_run_id=new_id("poll"),
    )
    extractor = _RetryExtractor()
    hub = ContentEnrichmentHub(
        repository, bus, extractor=extractor, concurrency=8, retry_delay_seconds=0
    )

    assert await hub.run_once() == 1
    assert len(repository.list_enrichment_jobs()) == 1
    assert repository.list_raw(ticker="MU") == []
    assert await hub.run_once() == 1

    raw = repository.list_raw(ticker="MU")[0]
    assert raw.body == "Recovered full article body"
    assert raw.publisher_name == "Reuters"
    assert raw.resolved_domain == "publisher.test"
    assert raw.metadata["media_enrichment"]["attempts"][0]["status_code"] == 429
    assert extractor.calls == 2


class _ForbiddenExtractor:
    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        raise AssertionError("blacklisted short-message sources must not make network calls")


async def test_blacklist_skips_network_and_body_falls_back_to_summary(tmp_path: Path) -> None:
    repository, bus, source, binding = _setup(tmp_path)
    source = source.model_copy(update={"content_enrichment_mode": ContentEnrichmentMode.SKIP})
    bus.enqueue_enrichment(
        source=source,
        binding=binding,
        message=_message(2, summary="summary survives"),
        bootstrap=False,
        poll_run_id=new_id("poll"),
    )
    hub = ContentEnrichmentHub(repository, bus, extractor=_ForbiddenExtractor())

    assert await hub.run_once() == 1
    raw = repository.list_raw(ticker="MU")[0]
    assert raw.body == "summary survives"
    assert raw.metadata["media_enrichment"]["reason"] == "source_blacklist"


class _PermanentFailureExtractor:
    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        return MediaExtractionResult(
            record=record,
            reason="http_404",
            http_status=404,
            final_url=record.url,
        )


async def test_permanent_failure_keeps_native_body_without_retry(tmp_path: Path) -> None:
    repository, bus, source, binding = _setup(tmp_path)
    bus.enqueue_enrichment(
        source=source,
        binding=binding,
        message=_message(3, body="native body", summary="lower priority summary"),
        bootstrap=False,
        poll_run_id=new_id("poll"),
    )
    hub = ContentEnrichmentHub(repository, bus, extractor=_PermanentFailureExtractor())

    assert await hub.run_once() == 1
    assert repository.list_enrichment_jobs() == []
    raw = repository.list_raw(ticker="MU")[0]
    assert raw.body == "native body"
    assert raw.metadata["v2_body_completion"]["attempt_count"] == 1


async def test_expired_retry_is_discarded_to_fallback(tmp_path: Path) -> None:
    repository, bus, source, binding = _setup(tmp_path)
    bus.enqueue_enrichment(
        source=source,
        binding=binding,
        message=_message(4, summary="deadline fallback"),
        bootstrap=False,
        poll_run_id=new_id("poll"),
        collected_at=datetime.now(UTC) - timedelta(seconds=181),
    )
    extractor = _RetryExtractor()
    hub = ContentEnrichmentHub(repository, bus, extractor=extractor, retry_delay_seconds=0)

    assert await hub.run_once() == 1
    assert repository.list_enrichment_jobs() == []
    assert repository.list_raw(ticker="MU")[0].body == "deadline fallback"
    assert extractor.calls == 1
