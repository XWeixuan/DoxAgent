from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.message_bus_v2.google_news_rules import (
    GOOGLE_NEWS_SOURCE_ID,
    filter_google_news_result,
    google_narrow_query,
    google_news_blocklist,
)
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.news_adapters import GoogleNewsSearchRssAdapter
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import PollResult, RawMessageInput, UpdateActor, utc_now
from doxagent.message_bus_v2.search_plan import build_query_plan
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings


def message(host: str) -> RawMessageInput:
    return RawMessageInput(
        external_id=host,
        title="Company news",
        source="Publisher",
        url="https://news.google.com/rss/articles/wrapper",
        published_at=utc_now(),
        raw_payload={"source_url": "https://" + host},
        metadata={"publisher_domain": host},
    )


@pytest.mark.parametrize(
    ("ticker", "first", "query"),
    [
        ("MU", "Micron", "Micron"),
        ("BE", "Bloom Energy", '"Bloom Energy"'),
        ("INTC", "intc", "intc"),
        ("RKLB", "Rocket Lab", '"Rocket Lab"'),
        ("SNOW", "Snowflake", "Snowflake"),
        ("CEG", "Constellation Energy", '"Constellation Energy"'),
    ],
)
def test_narrow_query_uses_first_literal_without_adding_ticker(ticker, first, query):
    assert google_narrow_query(ticker, first) == query


def test_missing_first_term_never_falls_back_to_ticker_or_broad_news():
    with pytest.raises(ValueError, match="CONFIG_INCOMPLETE"):
        google_narrow_query("RKLB", "  ")


@pytest.mark.parametrize(
    ("ticker", "company", "expected"),
    [
        ("BE", "Bloom Energy", '"Bloom Energy"'),
        ("INTC", "Intel", "Intel"),
        ("MU", "Micron", "Micron"),
    ],
)
def test_google_plan_is_narrow_for_every_ticker_but_other_search_plans_are_not(
    ticker,
    company,
    expected,
):
    terms = TickerMonitoringTerms.model_validate(
        {
            "ticker": ticker,
            "expected_revision": 0,
            "l1_concepts": [
                {"concept_id": key, "expressions": {"en": value}}
                for key, value in [
                    ("company", company),
                    ("sector", "foundry"),
                    ("topic", "fuel cell"),
                ]
            ],
            "l2": {},
            "definition": {"relevant": "Relevant", "irrelevant": "Other"},
        }
    )
    sources = {s.source_id: s for s in initial_sources()}
    google = build_query_plan(sources[GOOGLE_NEWS_SOURCE_ID], terms, 1)
    assert len(google.queries) == 1
    assert google.queries[0].query == expected
    assert "sector" not in google.queries[0].concept_ids
    assert "ticker_symbol" not in google.queries[0].concept_ids
    reuters = build_query_plan(sources["reuters_site_search"], terms, 1)
    assert [q.query for q in reuters.queries] == [
        company if " " not in company else f'"{company}"',
        "foundry",
        '"fuel cell"',
    ]
    terms.l1_concepts[0].expressions["en"] = company + " Holdings"
    changed = build_query_plan(sources[GOOGLE_NEWS_SOURCE_ID], terms, 2)
    assert changed.queries[0].query_key != google.queries[0].query_key
    assert "Holdings" in changed.queries[0].query


@pytest.mark.parametrize(
    "host",
    [
        "on3.com",
        "www.247sports.com",
        "news.pcguide.com",
        "media.unisba.ac.id",
        "AD-HOC-NEWS.DE.",
        "news.marketbeat.com",
        "finance.biggo.com",
        "biggo.com",
        "digitalfoundry.net",
        "simplywall.st",
        "shattered.io",
        "tradingkey.com",
        "finance.aol.com",
        "aol.co.uk",
        "news.pluang.com",
        "tradersunion.com",
        "foreignpolicyjournal.com",
        "kalkinemedia.com",
        "stockstotrade.com",
        "timothysykes.com",
        "trefis.com",
        "tokenpost.com",
        "kalkine.ca",
        "maxpreps.com",
    ],
)
def test_blocklist_covers_reviewed_publishers_and_their_subdomains(host):
    assert google_news_blocklist().blocks(host)


@pytest.mark.parametrize(
    "host",
    [
        "notmarketbeat.com",
        "marketbeat.com.example.org",
        "reuters.com",
        "",
        "unisba.ac.id",
        "other.unisba.ac.id",
        "seekingalpha.com",
    ],
)
def test_domain_boundaries_and_unknown_publishers_preserve_coverage(host):
    assert not google_news_blocklist().blocks(host)


def test_google_filter_is_idempotent_and_never_filters_other_sources():
    original = PollResult(messages=[message("on3.com"), message("reuters.com")])
    for source_id in ("yahoo_finance_news", "reuters_site_search", "another_source"):
        assert filter_google_news_result(source_id, original) is original
    filtered = filter_google_news_result(GOOGLE_NEWS_SOURCE_ID, original)
    again = filter_google_news_result(GOOGLE_NEWS_SOURCE_ID, filtered)
    assert len(again.messages) == 1
    assert again.acquisition_metadata["google_news_blacklist_filtered_count"] == 1
    assert again.acquisition_metadata["google_news_blacklist_filtered_sites"] == {"on3.com": 1}


@pytest.mark.parametrize("managed_plan", [False, True])
async def test_adapter_discards_blacklisted_rows_without_resolving_wrappers(
    tmp_path,
    monkeypatch,
    managed_plan,
):
    from tests.test_message_bus_v2_news_sources import NOW, _context

    resolve = AsyncMock(side_effect=AssertionError("listing must not decode wrappers"))
    monkeypatch.setattr("doxagent.message_bus_v2.google_news.resolve_google_news_urls", resolve)
    items = "".join(
        f"<item><guid>{host}</guid><title>Company news</title>"
        f"<link>https://news.google.com/rss/articles/{host}</link>"
        f"<pubDate>{NOW.isoformat()}</pubDate>"
        f'<source url="https://{host}">Publisher</source></item>'
        for host in ("on3.com", "finance.biggo.com", "reuters.com")
    )
    context, _ = _context(tmp_path, GOOGLE_NEWS_SOURCE_ID, {"search_terms": ["Micron"]})
    requests = []

    def listing(request):
        requests.append(request)
        return httpx.Response(200, text="<rss><channel>" + items + "</channel></rss>")

    context = context.model_copy(
        update={
            "binding": context.binding.model_copy(
                update={
                    "source_parameters": {"search_terms": ["Micron", "foundry", "fuel cell"]},
                }
            )
        }
    )
    if managed_plan:
        terms = TickerMonitoringTerms.model_validate(
            {
                "ticker": "MU",
                "expected_revision": 0,
                "l1_concepts": [
                    {"concept_id": key, "expressions": {"en": value}}
                    for key, value in [("company", "Micron"), ("sector", "foundry")]
                ],
                "l2": {},
                "definition": {"relevant": "Relevant", "irrelevant": "Other"},
            }
        )
        context = context.model_copy(
            update={
                "query_plan": build_query_plan(context.source, terms, 1).model_dump(mode="json"),
            }
        )
    async with httpx.AsyncClient(transport=httpx.MockTransport(listing)) as client:
        result = await GoogleNewsSearchRssAdapter(DoxAgentSettings(_env_file=None), client).poll(
            context
        )
    assert [m.metadata["publisher_domain"] for m in result.messages] == ["reuters.com"]
    assert result.acquisition_metadata["google_news_blacklist_filtered_count"] == 2
    assert requests[0].url.params["q"] == "Micron when:1d"
    assert result.acquisition_metadata["query_rule"] == "first_l1_only"
    assert resolve.await_count == 0


@pytest.mark.parametrize("ticker", ["BE", "INTC", "MU"])
async def test_google_filter_protects_all_tickers_and_durable_intake(tmp_path, ticker):
    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    source = bus.require_source(GOOGLE_NEWS_SOURCE_ID)
    binding = bus.configure_binding(
        ticker=ticker,
        source_id=source.source_id,
        source_parameters={"search_terms": ["Company"]},
        actor=UpdateActor.SYSTEM,
    )
    blocked = message("news.marketbeat.com")
    # This bypasses the adapter, as a previously persisted capture can.
    receipt = await bus.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(messages=[blocked]),
    )
    assert receipt.queued_count == 0
    assert repo.snapshot_counts()["raw_messages"] == 0
    assert repo.snapshot_counts()["content_enrichment_jobs"] == 0
    assert bus.enqueue_enrichment(
        source=source,
        binding=binding,
        message=blocked,
        bootstrap=False,
        poll_run_id="direct-test",
    ) == (None, False)


async def test_google_blacklist_does_not_stop_yahoo_body_intake(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    source = bus.require_source("yahoo_finance_news")
    binding = bus.configure_binding(
        ticker="MU", source_id=source.source_id, actor=UpdateActor.SYSTEM
    )
    receipt = await bus.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(messages=[message("marketbeat.com")]),
    )
    assert receipt.queued_count == 1
    assert repo.snapshot_counts()["content_enrichment_jobs"] == 1
