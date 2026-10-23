import asyncio
from datetime import UTC, datetime, timedelta

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.monitoring.media_enrichment import FetchAttempt, MediaExtractionResult
from tests.test_content_enrichment_hub import _message, _setup


class Extractor:
    def __init__(self):
        self.calls = 0

    async def extract(self, record):
        self.calls += 1
        await asyncio.sleep(0.05)
        return MediaExtractionResult(
            record=record,
            content=(f"Company confirmed investment {self.calls} billion dollars. " * 30),
            final_url=record.url,
            attempts=(FetchAttempt("direct", record.url, status_code=200),),
            diagnostics={
                "outcome": "FULL",
                "identity_match": "supported",
                "pipeline_version": "body_v2.2",
            },
        )


async def test_nine_bindings_download_once_and_keep_distinct_publication_receipts(tmp_path):
    repo, bus, source, _ = _setup(tmp_path)
    message = _message(9001, summary="Investment announcement.")
    for ticker in ("MU", "INTC", "BE", "COHR", "NBIS", "CEG", "RKLB", "SNOW", "ONDS"):
        if ticker != "MU":
            bus.start_ticker(ticker)
        binding = repo.get_binding(ticker + ":" + source.source_id)
        bus.enqueue_enrichment(
            source=source,
            binding=binding,
            message=message,
            bootstrap=False,
            poll_run_id="same-article",
        )
    extractor = Extractor()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor, concurrency=8)
    await hub.run_once()
    await asyncio.sleep(1.05)
    await hub.run_once()
    assert extractor.calls == 1
    assert not repo.list_enrichment_jobs()
    receipts = repo.list_enrichment_jobs(include_terminal=True)
    assert len(receipts) == 9 and all(j.status.value == "completed" for j in receipts)
    assert len({j.body_result_key for j in receipts}) == 1
    for ticker in ("MU", "INTC", "BE", "COHR", "NBIS", "CEG", "RKLB", "SNOW", "ONDS"):
        assert repo.latest_stream_offset(ticker) == 1
    with repo._connect() as db:
        assert (
            db.execute("SELECT count(*) FROM article_body_results WHERE status='READY'").fetchone()[
                0
            ]
            == 1
        )
        assert (
            db.execute(
                "SELECT count(*) FROM audit_log WHERE entity_type='body_completion'"
            ).fetchone()[0]
            == 9
        )
        assert db.execute("SELECT count(*) FROM site_strategy_result_outbox").fetchone()[0] == 1


async def test_known_old_article_updates_original_without_republishing(tmp_path, monkeypatch):
    repo, bus, source, binding = _setup(tmp_path)
    now = datetime.now(UTC)
    original = _message(9002, summary="First investment report.")
    bus.enqueue_enrichment(
        source=source, binding=binding, message=original, bootstrap=False, poll_run_id="first"
    )
    extractor = Extractor()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    await hub.run_once()
    raw = repo.list_raw(ticker="MU")[0]
    later = now + timedelta(hours=1)
    for module in (
        "doxagent.message_bus_v2.service",
        "doxagent.message_bus_v2.repository",
        "doxagent.message_bus_v2.schema",
        "doxagent.message_bus_v2.body_results",
        "doxagent.content_enrichment.service",
    ):
        monkeypatch.setattr(module + ".utc_now", lambda: later)
    updated = original.model_copy(update={"summary": "Company doubles the investment."})
    job, created = bus.enqueue_enrichment(
        source=source,
        binding=binding,
        message=updated,
        bootstrap=False,
        poll_run_id="update",
        collected_at=later,
    )
    assert created and job.known_article_raw_id == raw.raw_message_id
    await hub.run_once(now=later)
    assert extractor.calls == 2
    assert repo.latest_stream_offset("MU") == 1
    current = repo.get_raw(raw.raw_message_id)
    assert "investment 2" in current.body
    assert current.metadata["message_version"]["content_revision"] == 2
    assert len(repo.list_raw(ticker="MU")) == 1


async def test_each_real_retry_has_one_bus_and_site_receipt(tmp_path):
    repo, bus, source, binding = _setup(tmp_path)
    bus.enrichment_pipeline_version = "body_v2.2"

    class RetryingExtractor(Extractor):
        async def extract(self, record):
            if not self.calls:
                self.calls += 1
                return MediaExtractionResult(
                    record=record,
                    reason="http_429",
                    attempts=(
                        FetchAttempt(
                            "direct",
                            record.url,
                            status_code=429,
                            reason="http_429",
                            transient_hint=True,
                        ),
                    ),
                    diagnostics={"outcome": "UNAVAILABLE", "pipeline_version": "body_v2.2"},
                )
            return await super().extract(record)

    bus.enqueue_enrichment(
        source=source, binding=binding, message=_message(9003), bootstrap=False, poll_run_id="retry"
    )
    hub = ContentEnrichmentHub(repo, bus, extractor=RetryingExtractor(), retry_delay_seconds=0)
    assert await hub.run_once() == 1
    assert await hub.run_once() == 1
    with repo._connect() as db:
        audits = db.execute(
            "SELECT audit_id FROM audit_log WHERE entity_type='body_completion'"
        ).fetchall()
        receipts = db.execute("SELECT outbox_id FROM site_strategy_result_outbox").fetchall()
    assert len(audits) == len(receipts) == 2
    assert {r[0] for r in audits} == {r[0] for r in receipts}
    assert repo.list_enrichment_jobs(include_terminal=True)[0].execution_seq == 2


async def test_legacy_adoption_cannot_cross_paid_publisher_scope(tmp_path):
    from doxagent.message_bus_v2.body_results import ArticleBodyResults

    repo, bus, source, binding = _setup(tmp_path)
    original = _message(9005).model_copy(update={"url": "https://www.reuters.com/news/9005"})
    bus.enqueue_enrichment(
        source=source, binding=binding, message=original, bootstrap=False, poll_run_id="legacy"
    )
    await ContentEnrichmentHub(repo, bus, extractor=Extractor()).run_once()
    results = ArticleBodyResults(repo)
    other = original.model_copy(
        update={"metadata": {"body_observation_source_id": "different-source"}}
    )
    row = results.adopt_legacy(other, "different-source", "MU", now=datetime.now(UTC))
    assert not row.get("content")
