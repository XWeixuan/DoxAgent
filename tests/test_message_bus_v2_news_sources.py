from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from doxagent.message_bus_v2.google_news import resolve_google_news_urls
from doxagent.message_bus_v2.ibkr_news import IbkrNewsAdapter, _parse_error_args, _published
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.news_adapters import (
    GoogleNewsSearchRssAdapter,
    ReutersSiteSearchAdapter,
    YahooFinanceNewsAdapter,
)
from doxagent.message_bus_v2.news_policy import HiddenNewsIngressPolicy
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import PollContext, PollResult, RawMessageInput, UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def yahoo_mock_transport(monkeypatch):
    # Keep existing provider fixtures on MockTransport, never make live requests.
    from doxagent.message_bus_v2.yahoo_transport import YahooTransport

    original = YahooFinanceNewsAdapter.__init__
    transports = []

    def initialize(self, settings, client, **kwargs):
        transport = YahooTransport(session_factory=lambda **_: client, gap=0, jitter=0)
        transports.append(transport)
        original(self, settings, client, transport=transport)

    monkeypatch.setattr(YahooFinanceNewsAdapter, "__init__", initialize)
    yield
    for transport in transports:
        transport.close()


@asynccontextmanager
async def _permit() -> AsyncIterator[None]:
    yield


def _context(
    tmp_path: Path, source_id: str, parameters: dict[str, object]
) -> tuple[PollContext, MessageBusV2Repository]:
    repository = MessageBusV2Repository(tmp_path / f"{source_id}.sqlite3")
    service = MessageBusV2Service(repository)
    service.bootstrap()
    source = service.require_source(source_id)
    binding = service.configure_binding(
        ticker="MU",
        source_id=source_id,
        source_parameters=parameters,
        actor=UpdateActor.SYSTEM,
    )
    return (
        PollContext(
            ticker="MU",
            source=source,
            binding=binding,
            requested_at=NOW,
            request_permit=_permit,
        ),
        repository,
    )


async def test_yahoo_ncp_filters_24h_and_preserves_publisher_domain(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/xhr/ncp"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "content": {
                            "id": "new",
                            "title": "New Micron story",
                            "summary": "summary",
                            "pubDate": "2026-09-14T10:00:00Z",
                            "provider": {
                                "displayName": "Reuters",
                                "url": "https://www.reuters.com",
                            },
                            "canonicalUrl": {"url": "https://finance.yahoo.com/news/new"},
                        }
                    },
                    {
                        "content": {
                            "id": "old",
                            "title": "Old story",
                            "pubDate": "2026-09-12T10:00:00Z",
                            "canonicalUrl": {"url": "https://finance.yahoo.com/news/old"},
                        }
                    },
                ]
            },
        )

    context, _ = _context(tmp_path, "yahoo_finance_news", {})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await YahooFinanceNewsAdapter(DoxAgentSettings(_env_file=None), client).poll(context)
    assert [item.external_id for item in result.messages] == ["new"]
    assert result.messages[0].metadata["publisher_domain"] == "reuters.com"
    assert result.acquisition_metadata["query_mode"] == "ncp_latest_news"
    await client.aclose()


async def test_yahoo_search_fallback_is_bounded_to_ten(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/xhr/ncp":
            return httpx.Response(404)
        assert request.url.params["newsCount"] == "10"
        return httpx.Response(200, json={"news": []})

    context, _ = _context(tmp_path, "yahoo_finance_news", {"snippet_count": 200})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await YahooFinanceNewsAdapter(DoxAgentSettings(_env_file=None), client).poll(context)
    assert result.messages == []
    assert result.window_coverage == "PARTIAL"
    assert result.acquisition_metadata["requested_count"] == 10
    await client.aclose()


async def test_yahoo_reader_proxy_is_last_fallback(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host != "r.jina.ai":
            return httpx.Response(404)
        assert request.url.params["newsCount"] == "10"
        payload = {
            "news": [
                {
                    "uuid": "proxy-new",
                    "title": "Micron proxy result",
                    "publisher": "Yahoo Finance",
                    "link": "https://finance.yahoo.com/news/proxy-new",
                    "providerPublishTime": int(NOW.timestamp()),
                }
            ]
        }
        return httpx.Response(
            200,
            text="Title: \n\nURL Source: upstream\n\nMarkdown Content:\n"
            + json.dumps(payload),
        )

    context, _ = _context(tmp_path, "yahoo_finance_news", {})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await YahooFinanceNewsAdapter(DoxAgentSettings(_env_file=None), client).poll(context)
    assert [item.external_id for item in result.messages] == ["proxy-new"]
    assert result.window_coverage == "PARTIAL"
    assert result.acquisition_metadata["query_mode"] == (
        "finance_search_reader_proxy_fallback"
    )
    assert result.acquisition_metadata["endpoint"] == "query1_via_reader_proxy"
    assert [request.url.host for request in requests] == [
        "finance.yahoo.com",
        "query1.finance.yahoo.com",
        "query2.finance.yahoo.com",
        "r.jina.ai",
    ]
    await client.aclose()


async def test_google_rss_terms_domains_and_24h_filter(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/rss/search"):
            return httpx.Response(
                200,
                text=(
                    "<rss><channel><item><guid>g1</guid><title>Micron result</title>"
                    "<description>description</description>"
                    "<link>https://news.google.com/rss/articles/notbase64</link>"
                    "<pubDate>Mon, 14 Sep 2026 10:00:00 GMT</pubDate>"
                    '<source url="https://www.reuters.com">Reuters</source>'
                    "</item></channel></rss>"
                ),
            )
        return httpx.Response(200, text="")

    context, _ = _context(
        tmp_path,
        "google_news_search_rss",
        {"search_terms": ["micron", "MU"], "domains": ["reuters.com"]},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await GoogleNewsSearchRssAdapter(DoxAgentSettings(_env_file=None), client).poll(
        context
    )
    assert len(result.messages) == 1
    assert result.messages[0].metadata["publisher_domain"] == "reuters.com"
    assert "site:reuters.com" in requests[0].url.params["q"]
    assert "when:1d" in requests[0].url.params["q"]
    await client.aclose()


async def test_google_modern_wrapper_resolves_with_signature_protocol() -> None:
    calls = 0

    @asynccontextmanager
    async def counted_permit() -> AsyncIterator[None]:
        nonlocal calls
        calls += 1
        yield

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                text='<c-wiz><div jscontroller="x" data-n-a-sg="sig" data-n-a-ts="123"/></c-wiz>',
            )
        payload = json.dumps(["garturlres", "https://publisher.test/article", 1])
        return httpx.Response(
            200,
            text=")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", payload]]),
        )

    wrapper = "https://news.google.com/rss/articles/modern-token?oc=5"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resolved = await resolve_google_news_urls(client, [wrapper], counted_permit)
    assert resolved[wrapper] == "https://publisher.test/article"
    assert calls == 2
    await client.aclose()


async def test_reuters_uses_today_and_previous_day_only(tmp_path: Path) -> None:
    class Browser:
        async def reuters_search(self, query: str, offset: int) -> list[dict[str, object]]:
            assert query == "micron"
            assert offset == 0
            return [
                {
                    "title": "Micron current result title",
                    "url": "/technology/micron-current-2026-09-14/",
                    "date": "September 14, 2026",
                },
                {
                    "title": "Micron boundary result title",
                    "url": "/technology/micron-boundary-2026-09-13/",
                    "date": "September 13, 2026",
                },
                {
                    "title": "Micron old result title",
                    "url": "/technology/micron-old-2026-09-12/",
                    "date": "September 12, 2026",
                },
            ]

    context, _ = _context(
        tmp_path,
        "reuters_site_search",
        {"company_short_name": "micron", "max_pages": 2},
    )
    result = await ReutersSiteSearchAdapter(Browser()).poll(context)
    assert len(result.messages) == 2
    assert {item.metadata["search_result_date"] for item in result.messages} == {
        "2026-09-13",
        "2026-09-14",
    }
    assert all(item.metadata["publication_time_precision"] == "day" for item in result.messages)
    assert result.acquisition_metadata["pages_fetched"] == 1
    assert result.acquisition_metadata["search_result_count"] == 3


async def test_reuters_default_binding_resolves_company_name_from_cdecr_catalog(
    tmp_path: Path,
) -> None:
    class Browser:
        async def reuters_search(self, query: str, offset: int) -> list[dict[str, object]]:
            assert query == "Micron"
            return []

    context, _ = _context(tmp_path, "reuters_site_search", {})
    result = await ReutersSiteSearchAdapter(Browser()).poll(context)
    assert result.messages == []
    assert result.acquisition_metadata["query_source"] == "cdecr_company_catalog"


async def test_reuters_unknown_ticker_falls_back_to_yahoo_symbol_lookup(tmp_path: Path) -> None:
    class Browser:
        async def reuters_search(self, query: str, offset: int) -> list[dict[str, object]]:
            assert query == "Example"
            return []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "query1.finance.yahoo.com"
        assert request.url.params["q"] == "NOTREAL123"
        return httpx.Response(200, json={"quotes": [{"shortname": "Example Technology, Inc."}]})

    context, _ = _context(tmp_path, "reuters_site_search", {})
    context = context.model_copy(update={"ticker": "NOTREAL123"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await ReutersSiteSearchAdapter(Browser(), client).poll(context)
    assert result.messages == []
    assert result.acquisition_metadata["query_source"] == "yahoo_symbol_lookup"
    await client.aclose()


async def test_ibkr_stream_rows_use_local_24h_filter(tmp_path: Path) -> None:
    class Gateway:
        def poll(self, ticker: str, since: datetime, until: datetime):
            assert ticker == "MU"
            return (
                [
                    {
                        "time": int((NOW - timedelta(hours=1)).timestamp()),
                        "providerCode": "DJ-N",
                        "articleId": "a1",
                        "headline": "Micron headline",
                        "articleText": "Full body https://example.test/a1",
                        "transport": "tick_292",
                    },
                    {
                        "time": int((NOW - timedelta(days=2)).timestamp()),
                        "providerCode": "DJ-N",
                        "articleId": "old",
                        "headline": "Old",
                    },
                ],
                {
                    "transport": "tick_292",
                    "provider_names": {"DJ-N": "Dow Jones"},
                    "provider_codes": ["DJ-N"],
                    "has_more": False,
                    "errors": [],
                },
            )

        def close(self) -> None:
            pass

    context, _ = _context(tmp_path, "ibkr_news", {})
    result = await IbkrNewsAdapter(DoxAgentSettings(_env_file=None), Gateway()).poll(context)
    assert len(result.messages) == 1
    assert result.messages[0].external_id == "DJ-N:a1"
    assert result.messages[0].publisher_name == "Dow Jones"
    assert result.window_coverage == "COMPLETE"


def test_ibkr_error_callback_supports_current_and_legacy_signatures() -> None:
    assert _parse_error_args((1789330454998, 2104, "farm connected", "")) == (
        2104,
        "farm connected",
    )
    assert _parse_error_args((504, "not connected", "")) == (504, "not connected")
    assert _published("1789330454998") == datetime.fromtimestamp(1789330454.998, UTC)


def test_yahoo_and_google_hidden_filter_is_empty_by_default_and_blocks_both_dimensions() -> None:
    sources = {item.source_id for item in initial_sources()}
    assert {
        "yahoo_finance_news",
        "ibkr_news",
        "reuters_site_search",
        "google_news_search_rss",
    } <= sources
    messages = [
        RawMessageInput(
            external_id="a",
            title="a",
            body="a",
            source="Allowed",
            publisher_name="Allowed",
            url="https://blocked.example/a",
            published_at=NOW,
            raw_payload={"id": "a"},
        ),
        RawMessageInput(
            external_id="b",
            title="b",
            body="b",
            source="Blocked Wire",
            publisher_name="Blocked Wire",
            url="https://allowed.example/b",
            published_at=NOW,
            raw_payload={"id": "b"},
        ),
    ]
    assert (
        len(
            HiddenNewsIngressPolicy()
            .apply("yahoo_finance_news", PollResult(messages=messages))
            .messages
        )
        == 2
    )
    policy = HiddenNewsIngressPolicy.from_strings(
        domains="blocked.example", publishers="Blocked Wire"
    )
    for source_id in ("yahoo_finance_news", "google_news_search_rss"):
        result = policy.apply(source_id, PollResult(messages=messages))
        assert result.messages == []
        assert result.acquisition_metadata["blocked_domain_count"] == 1
        assert result.acquisition_metadata["blocked_publisher_count"] == 1


async def test_hidden_filter_runs_before_raw_and_enrichment_persistence(tmp_path: Path) -> None:
    repository = MessageBusV2Repository(tmp_path / "hidden.sqlite3")
    service = MessageBusV2Service(
        repository,
        enrichment_queue_enabled=True,
        hidden_news_policy=HiddenNewsIngressPolicy.from_strings(domains="blocked.example"),
    )
    service.bootstrap()
    source = service.require_source("yahoo_finance_news")
    binding = service.configure_binding(
        ticker="MU",
        source_id=source.source_id,
        actor=UpdateActor.SYSTEM,
    )
    message = RawMessageInput(
        external_id="blocked",
        title="blocked",
        body="blocked",
        source="Allowed",
        url="https://blocked.example/a",
        published_at=NOW,
        raw_payload={"id": "blocked"},
    )
    output = await service.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(messages=[message]),
        attempted_at=NOW,
    )
    counts = repository.snapshot_counts()
    assert output.collected_count == 0
    assert counts["raw_messages"] == 0
    assert counts["content_enrichment_jobs"] == 0
