import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import PollContext, UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.technology_sources import Capture
from doxagent.message_bus_v2.thefly_source import (
    SOURCE_ID,
    TheFlyAdapter,
    TheFlyBodyReader,
    initial_source,
    parse_news,
)
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from scripts.ops.configure_technology_sources import configure

FIXTURE = Path(__file__).parent / "fixtures/technology_sources/thefly-public.json"
NOW = datetime(2026, 10, 26, tzinfo=UTC)


@asynccontextmanager
async def permit():
    yield


def context(checkpoint=None, at=NOW, **kwargs):
    from doxagent.message_bus_v2.schema import TickerSourceBinding

    return PollContext(
        ticker="MU",
        source=initial_source(),
        binding=TickerSourceBinding(binding_id="MU:" + SOURCE_ID, ticker="MU", source_id=SOURCE_ID),
        checkpoint=checkpoint or {},
        requested_at=at,
        request_permit=permit,
        **kwargs,
    )


def record(message):
    return MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=SOURCE_ID,
        ticker="MU",
        title=message.title,
        body=None,
        url=message.url,
    )


def test_public_feed_is_ticker_specific_and_eastern_time_is_not_utc():
    raw = FIXTURE.read_text(encoding="utf-8")
    _, mu = parse_news(raw, "MU")
    _, snow = parse_news(raw, "SNOW")
    assert len(mu) == 1 and len(snow) == 1
    assert mu[0].published_at == datetime(2026, 10, 23, 20, 11, 41, tzinfo=UTC)
    assert "id=4448423" in mu[0].url and mu[0].source_item_key == "4448423"
    assert mu[0].body is None and mu[0].metadata["body_access_scope"] == "public:thefly"


async def test_public_native_full_body_and_truncated_preview_are_separated_without_network():
    raw = FIXTURE.read_text(encoding="utf-8")
    _, full = parse_news(raw, "MU")
    _, preview = parse_news(raw, "SNOW")
    http = SimpleNamespace(
        fetch=AsyncMock(side_effect=AssertionError("native API must not refetch or launch browser"))
    )
    reader = TheFlyBodyReader(http)
    result = await reader.extract(
        SimpleNamespace(source=initial_source(), message=full[0]), record(full[0])
    )
    assert result.succeeded and result.diagnostics["outcome"] == "FULL"
    assert "Micron" in result.content
    result = await reader.extract(
        SimpleNamespace(source=initial_source(), message=preview[0]), record(preview[0])
    )
    assert not result.succeeded and result.reason == "subscription_required"
    assert result.diagnostics["outcome"] == "PARTIAL"
    http.fetch.assert_not_awaited()


async def test_unchanged_public_feed_is_not_reenriched_but_body_changes_remain_visible():
    raw = FIXTURE.read_text(encoding="utf-8")
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, "", raw, {})))
    adapter = TheFlyAdapter(http)
    first = await adapter.poll(context())
    assert len(first.messages) == 1
    second = await adapter.poll(context(first.next_checkpoint, NOW + timedelta(seconds=60)))
    assert not second.messages
    changed = json.loads(raw)
    changed["news"][0]["description"] += (
        "<p>A new financing agreement materially changes the transaction terms.</p>"
    )
    http.fetch.return_value = Capture(200, "", json.dumps(changed), {})
    third = await adapter.poll(context(second.next_checkpoint, NOW + timedelta(seconds=120)))
    assert len(third.messages) == 1
    assert (
        third.messages[0].metadata["identity_evidence"]["provider_version"]
        != first.messages[0].metadata["identity_evidence"]["provider_version"]
    )
    assert third.messages[0].url == first.messages[0].url


async def test_more_page_is_read_only_cursor_pagination_when_head_does_not_cover_window():
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))["news"][0]
    rows = [{**base, "id": str(5000000 + i), "date": f"2026-10-25 19:{i:02}:00"} for i in range(50)]
    tail = [{**base, "date": "2026-10-24 12:00:00"}]
    http = SimpleNamespace(
        fetch=AsyncMock(
            side_effect=[
                Capture(200, "", json.dumps({"accessType": "anonymous", "news": rows}), {}),
                Capture(200, "", json.dumps({"accessType": "anonymous", "news": tail}), {}),
            ]
        )
    )
    result = await TheFlyAdapter(http).poll(context(window_start=NOW - timedelta(hours=2)))
    assert result.window_coverage == "COMPLETE" and len(result.messages) == 51
    assert http.fetch.call_args.kwargs["method"] == "POST"
    assert http.fetch.call_args.kwargs["json_body"]["since"] == "2026-10-25 19:00:00"


@pytest.mark.parametrize("complete", [True, False])
async def test_real_hub_publishes_only_complete_fly_articles(tmp_path, complete):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    bus.start_ticker("MU")
    binding = bus.configure_binding(ticker="MU", source_id=SOURCE_ID, actor=UpdateActor.SYSTEM)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = data["news"][0 if complete else 1]
    row["stocks"] = ["MU"]
    row["date"] = utc_now().astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")
    _, messages = parse_news(json.dumps({"accessType": "anonymous", "news": [row]}), "MU")
    bus.enqueue_enrichment(
        source=initial_source(),
        binding=binding,
        message=messages[0],
        bootstrap=False,
        poll_run_id="fly-test",
    )
    hub = ContentEnrichmentHub(repo, bus)
    try:
        assert await hub.run_once() == 1
        assert len(repo.list_raw(ticker="MU")) == int(complete)
        assert len(repo.list_standard(ticker="MU")) == int(complete)
        if complete:
            assert repo.list_standard(ticker="MU")[0].body
    finally:
        await hub.close()


def test_configure_fly_binds_registered_tickers_and_keeps_future_ionq_reserved(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    service = MessageBusV2Service(repo)
    service.bootstrap()
    service.start_ticker("SNOW")
    report = configure(service, include_fly=True)
    assert repo.get_binding("SNOW:" + SOURCE_ID).enabled
    assert repo.get_ticker_state("IONQ") is None
    assert len(report["bindings"]) == 9  # four software groups, four common sources, Fly
    assert configure(service, include_fly=True)["initial_terms"] == []
