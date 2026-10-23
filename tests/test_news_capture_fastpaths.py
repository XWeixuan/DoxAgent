import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy import investing_document
from tests.test_investing_recovery import LISTING, URL


async def test_investing_ready_dom_does_not_wait_for_slow_response_body(monkeypatch):
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(investing_document, "response_listing", stalled)
    monkeypatch.setattr(
        investing_document, "capture_document", AsyncMock(return_value=(LISTING, URL))
    )
    page = SimpleNamespace(content=AsyncMock(side_effect=AssertionError("duplicate DOM")), url=URL)
    result = await asyncio.wait_for(
        investing_document.capture_listing(
            page, None, None, "owned", URL, "INTC", timeout=10, max_bytes=8000000
        ),
        3,
    )
    assert result == (LISTING, URL) and cancelled.is_set()


async def test_investing_ready_native_response_does_not_wait_for_generic_dom(monkeypatch):
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(investing_document, "capture_document", stalled)
    monkeypatch.setattr(investing_document, "response_listing", AsyncMock(return_value=LISTING))
    page = SimpleNamespace(content=AsyncMock(return_value=LISTING), url=URL)
    result = await asyncio.wait_for(
        investing_document.capture_listing(
            page, None, None, "owned", URL, "INTC", timeout=10, max_bytes=8000000
        ),
        3,
    )
    assert result == (LISTING, URL) and cancelled.is_set()


async def test_investing_current_challenge_wins_over_old_healthy_response(monkeypatch):
    challenge = "<title>Verify you are human</title>"

    async def document(*args, **kwargs):
        await asyncio.sleep(0.02)
        return challenge, URL

    monkeypatch.setattr(investing_document, "capture_document", document)
    monkeypatch.setattr(investing_document, "response_listing", AsyncMock(return_value=LISTING))
    page = SimpleNamespace(content=AsyncMock(return_value=challenge), url=URL)
    result = await investing_document.capture_listing(
        page, None, None, "owned", URL, "INTC", timeout=10, max_bytes=8000000
    )
    assert result == (challenge, URL)


async def test_capture_to_full_body_and_article_once_publication_receipt(tmp_path):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.polling import CaptureIntake
    from doxagent.message_bus_v2.schema import PollResult, utc_now
    from tests.test_body_completion_receipts import Extractor
    from tests.test_content_enrichment_hub import _message, _setup

    repo, bus, source, binding = _setup(tmp_path)
    journal = repo.polling
    message = _message(81000)
    at = utc_now()
    journal.commit(
        journal.prepare(
            source=source,
            binding=binding,
            result=PollResult(messages=[message]),
            run_id="capture-to-stream",
            observed_at=at,
            bootstrap=False,
        )
    )
    intake = CaptureIntake(bus)
    extractor = Extractor()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    try:
        receipt = await intake.wait("capture-to-stream")
        await hub.run_once()
        assert repo.latest_stream_offset("MU") == 1 and extractor.calls == 1
        clock = repo.get_poll_unit_state(binding, "HEAD")
        assert clock.last_first_capture_at == at
        assert (
            clock.last_first_intake_at >= at
            and clock.last_final_intake_at >= clock.last_first_intake_at
        )
        assert (
            repo.list_enrichment_jobs(include_terminal=True)[0].poll_run_id == "capture-to-stream"
        )
        assert receipt.queued_count == 1
        journal.commit(
            journal.prepare(
                source=source,
                binding=binding,
                result=PollResult(messages=[message]),
                run_id="repeat",
                observed_at=utc_now(),
                bootstrap=False,
            )
        )
        await intake.wait("repeat")
        await hub.run_once()
        assert repo.latest_stream_offset("MU") == 1 and extractor.calls == 1
    finally:
        await intake.close()
        await hub.close()


@pytest.mark.parametrize("original_bootstrap", [False, True])
async def test_yahoo_delayed_timestamp_keeps_original_publication_contract(
    tmp_path, original_bootstrap
):
    import httpx

    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.news_adapters import YahooFinanceNewsAdapter
    from doxagent.message_bus_v2.polling import CaptureIntake
    from doxagent.message_bus_v2.schema import PollResult
    from doxagent.settings import DoxAgentSettings
    from tests.test_body_completion_receipts import Extractor
    from tests.test_content_enrichment_hub import _message, _setup
    from tests.test_message_bus_v2_news_sources import _context

    context, _ = _context(tmp_path, "yahoo_finance_news", {})
    message = _message(84000)
    context = context.model_copy(
        update={
            "acquisition_unit": "TAIL:YAHOO_TIME",
            "is_bootstrap": not original_bootstrap,
            "checkpoint": {
                "pending_timestamp_rows": [{"link": message.url, "_bootstrap": original_bootstrap}]
            },
        }
    )
    async with httpx.AsyncClient() as client:
        adapter = YahooFinanceNewsAdapter(
            DoxAgentSettings(_env_file=None), client, transport=object()
        )
        adapter._poll_supplement = AsyncMock(
            return_value=PollResult(
                messages=[message], acquisition_metadata={"query_mode": "page_network_ncp"}
            )
        )
        result = await adapter.poll(context)
    assert result.messages[0].metadata["_poll_bootstrap"] is original_bootstrap
    # Replay the delayed article after the surrounding poll has changed bootstrap mode.
    repo, bus, _, _ = _setup(tmp_path / "replay")
    source = repo.get_source("yahoo_finance_news")
    binding = repo.get_binding("MU:yahoo_finance_news")
    journal = repo.polling
    journal.commit(
        journal.prepare(
            source=source,
            binding=binding,
            result=result,
            run_id="delayed-date",
            observed_at=message.published_at,
            bootstrap=not original_bootstrap,
        )
    )
    intake = CaptureIntake(bus)
    hub = ContentEnrichmentHub(repo, bus, extractor=Extractor())
    try:
        await intake.wait("delayed-date")
        await hub.run_once()
        assert repo.latest_stream_offset("MU") == int(not original_bootstrap)
    finally:
        await intake.close()
        await hub.close()
