from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from doxagent.content_enrichment.quality import inspect_html
from doxagent.message_bus_v2.manifests import initial_default_profile, initial_sources
from doxagent.message_bus_v2.market_sources import (
    GlobeNewswireSearchAdapter,
    MarketTickerAdapter,
    parse_globenewswire,
    parse_investing,
    parse_investorshub,
)
from doxagent.message_bus_v2.schema import PollContext, TickerSourceBinding

NOW = datetime(2026, 10, 2, 8, tzinfo=UTC)
SOURCES = {s.source_id: s for s in initial_sources()}


@asynccontextmanager
async def permit():
    yield


def context(source_id, ticker="MU", **kwargs):
    source = SOURCES[source_id]
    return PollContext(
        ticker=ticker,
        source=source,
        requested_at=NOW,
        binding=TickerSourceBinding(
            binding_id=f"{ticker}:{source_id}",
            ticker=ticker,
            source_id=source_id,
            source_parameters={},
        ),
        request_permit=permit,
        **kwargs,
    )


IH = """<div class="quote-news-item news-item"><h3>Micron announces memory expansion</h3>
<a href="/stock-market/NASDAQ/micron-technology-MU/stock-news/12345/memory-expansion">Read</a>
<div class="news-date-content">October 01 2026</div></div>"""
GNW = """<div class="newsLink pl-0"><div class="date-source">October 02, 2026 03:30 ET</div>
<div class="mainLink"><a href="/news-release/2026/10/02/123/0/en/chip-update.html">
Chip production capacity expansion</a></div><p>Capacity summary</p></div>"""
INV = """<main><h1>MU News</h1><article data-test="article-item">
<a href="/news/stock-market-news/chip-demand-12345"><img/></a>
<a data-test="article-title-link" href="/news/stock-market-news/chip-demand-12345">
Micron memory chip demand expands</a><time datetime="2026-10-02 07:30:00"/>
<p>Chip demand summary.</p></article></main>"""


def test_manifests_and_global_defaults():
    ids = {e.source_id for e in initial_default_profile().entries}
    for source_id in ["investorshub_ticker_news", "investing_ticker_news", "globenewswire_search"]:
        assert source_id in ids
        assert SOURCES[source_id].adapter_ref == "site:auto"
    assert "globenewswire_semiconductors_rss" not in ids
    for source_id in [*ids, "globenewswire_semiconductors_rss"]:
        assert SOURCES[source_id].default_polling_config.target_interval_seconds == 60


def test_investorshub_excludes_wrong_ticker_and_unscoped_news():
    doc = IH + IH.replace("micron-technology-MU", "intel-INTC").replace("12345", "67890")
    doc += '<a href="/stock-market/NASDAQ/MU/stock-news/99999/footer">Unrelated footer</a>'
    rows = parse_investorshub(doc, ticker="MU", now=NOW)
    assert len(rows) == 1 and rows[0].external_id == "investorshub:12345"
    assert rows[0].publication_time_basis == "DATE"
    assert rows[0].published_at.date().isoformat() == "2026-10-01"
    assert (
        parse_investorshub(IH.replace("October 01 2026", "yesterday"), ticker="MU", now=NOW) == []
    )


def test_globenewswire_exact_et_and_empty_vs_changed():
    rows = parse_globenewswire(GNW, now=NOW)
    assert rows[0].published_at == NOW.replace(hour=7, minute=30)
    assert rows[0].publication_time_basis == "EXACT"
    assert parse_globenewswire("<main>No results found</main>", now=NOW) == []
    with pytest.raises(RuntimeError):
        parse_globenewswire("<main>New unsupported layout</main>", now=NOW)


def test_investorshub_external_advfn_card_and_relative_day_hint():
    doc = """<div class="quote-news-item"><div class="news-content">
    <a href="https://uk.advfn.com/market-news/article/23881/micron-results">
    <h3>US stock futures advance after Micron results</h3></a></div>
    <div class="news-date-content">18 hours ago</div></div>"""
    rows = parse_investorshub(doc, ticker="MU", now=NOW)
    assert len(rows) == 1 and rows[0].external_id == "advfn-market:23881"
    assert rows[0].publication_time_basis == "DATE"
    assert rows[0].metadata["publisher_domain"] == "uk.advfn.com"
    assert rows[0].published_at.date().isoformat() == "2026-10-01"


def test_investing_title_link_and_utc_not_market_clock():
    rows = parse_investing(INV, ticker="MU", now=NOW)
    assert rows[0].title == "Micron memory chip demand expands"
    assert rows[0].published_at == NOW.replace(hour=7, minute=30)
    with pytest.raises(RuntimeError):
        parse_investing('<time datetime="2026-10-02T07:30:00Z"/>', ticker="MU", now=NOW)


@pytest.mark.asyncio
async def test_ticker_locator_uses_current_ticker_and_rejects_cross_site():
    seen = []

    class Browser:
        async def get(self, url, **kwargs):
            seen.append(url)
            return (
                200,
                "https://investorshub.advfn.com/stock-market/NASDAQ/micron-technology-MU/news",
                {},
                IH,
            )

    result = await MarketTickerAdapter(Browser(), "investorshub").poll(
        context("investorshub_ticker_news")
    )
    assert result.messages and "/NASDAQ/MU/news" in seen[0]
    ctx = context("investorshub_ticker_news")
    ctx.binding.source_parameters = {"listing_url": "https://attacker.test/news"}
    with pytest.raises(ValueError):
        await MarketTickerAdapter(Browser(), "investorshub").poll(ctx)


@pytest.mark.asyncio
async def test_search_queries_isolated_failure_and_recovery_checkpoint():
    calls = []

    class Browser:
        async def get(self, url, **kwargs):
            calls.append(url)
            if "bad" in url:
                raise RuntimeError("network failure")
            return 200, url, {}, GNW

    plan = {
        "terms_revision": 7,
        "queries": [{"query": "micron", "query_key": "good"}, {"query": "bad", "query_key": "bad"}],
    }
    adapter = GlobeNewswireSearchAdapter(Browser())
    first = await adapter.poll(context("globenewswire_search", query_plan=plan))
    assert len(first.messages) == 1 and len(first.failures) == 1
    assert not first.window_done
    second = await adapter.poll(
        context(
            "globenewswire_search",
            query_plan=plan,
            is_gap_recovery=True,
            checkpoint=first.next_checkpoint,
        )
    )
    assert second.window_done and second.window_coverage == "PARTIAL"
    assert sum("micron" in url for url in calls) == 1
    assert "page=1" in calls[0] and "page=3" not in calls[0]


@pytest.mark.asyncio
async def test_search_encodes_phrase_and_or_as_one_query():
    calls = []

    class Browser:
        async def get(self, url, **kwargs):
            calls.append(url)
            return 200, url, {}, "<main>No results found</main>"

    plan = {"queries": [{"query": '("Bloom Energy" OR fuel)', "query_key": "one"}]}
    result = await GlobeNewswireSearchAdapter(Browser()).poll(
        context("globenewswire_search", query_plan=plan)
    )
    assert result.window_done and result.window_coverage == "COMPLETE"
    assert len(calls) == 1 and "%22Bloom%20Energy%22%20OR%20fuel" in calls[0]


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-10-01T19:00:00-04:00", "2026-10-01T19:00:00-04:00"),
        ("2026-10-01", None),
        ("invalid", None),
    ],
)
def test_article_publication_requires_matching_headline_and_timezone(stamp, expected):
    obj = {
        "@type": "NewsArticle",
        "headline": "Micron announces memory expansion",
        "datePublished": stamp,
    }
    html = '<script type="application/ld+json">' + json.dumps(obj) + "</script>"
    inspected = inspect_html(html, "https://investorshub.advfn.com/article", obj["headline"])
    assert inspected.publisher_published_at == expected
    inspected = inspect_html(
        html, "https://investorshub.advfn.com/article", "Bloom Energy fuel cells"
    )
    assert inspected.publisher_published_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("age,published", [(5, True), (90, False)])
@pytest.mark.parametrize("source_id", ["investorshub_ticker_news", "investing_ticker_news"])
async def test_investorshub_verified_article_time_rechecks_realtime_admission(
    tmp_path, age, published, source_id
):
    from datetime import timedelta

    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.schema import RawMessageInput
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from doxagent.monitoring.media_enrichment import MediaExtractionResult

    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    bus.start_ticker("MU")
    source = bus.require_source(source_id)
    binding = repo.get_binding("MU:" + source_id)
    now = datetime.now(UTC)

    class Extractor:
        async def extract(self, record):
            return MediaExtractionResult(
                record=record,
                content="Full article content. " * 80,
                diagnostics={
                    "publisher_published_at": (now - timedelta(minutes=age)).isoformat(),
                    "identity_match": "supported",
                },
            )

    message = RawMessageInput(
        external_id="time-proof",
        title="Micron memory expansion",
        source="InvestorsHub",
        url="https://investorshub.advfn.com/article/123",
        published_at=now,
        publication_time_basis="DATE" if source_id == "investorshub_ticker_news" else "EXACT",
        raw_payload={"id": "time-proof"},
    )
    bus.enqueue_enrichment(
        source=source, binding=binding, message=message, bootstrap=False, poll_run_id="test"
    )
    hub = ContentEnrichmentHub(repo, bus, extractor=Extractor())
    assert await hub.run_once() == 1
    items = repo.list_standard(ticker="MU")
    assert bool(items) is published
    if published:
        assert items[0].publication_time_basis == "EXACT"
        assert items[0].published_at == now - timedelta(minutes=age)
    repo.close()
