from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import httpx

from doxagent.agents import default_agent_registry
from doxagent.models import AgentName, ResultStatus
from doxagent.monitoring.collectors import MonitoringCollectorRegistry
from doxagent.monitoring.repository import InMemoryMonitoringRepository, SQLiteMonitoringRepository
from doxagent.monitoring.schema import (
    FetchedExternalMessage,
    IngestBatchResult,
    InterfaceType,
    MonitoringParameters,
    SourceType,
    UpdateActor,
)
from doxagent.monitoring.service import MonitoringBusService, MonitoringPermissionError
from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.repository import InMemoryStocktwitsRepository
from doxagent.stocktwits.schema import BootstrapEventPolicy, StocktwitsPage, TickerMode
from doxagent.tools.providers.monitoring import MonitoringToolClient
from doxagent.tools.schema import ToolRequest


def _settings(**overrides: object) -> DoxAgentSettings:
    defaults: dict[str, object] = {
        "monitoring_storage_mode": "memory",
        "benzinga_api_key": "benzinga-key",
        "finnhub_api_key": "finnhub-key",
        "tikhub_api_key": "tikhub-key",
        "stocktwits_rapidapi_key": "stocktwits-key",
    }
    defaults.update(overrides)
    return DoxAgentSettings(**cast(Any, defaults))


def _request(tool_name: str, input_data: dict[str, object] | None = None) -> ToolRequest:
    return ToolRequest(
        tool_name=tool_name,
        ticker="AAPL",
        agent_name=AgentName.O2_MONITORING_CONFIG,
        input=input_data or {},
    )


def _long_article_body() -> str:
    paragraph = (
        "Micron shares advanced after analysts pointed to improving demand for high bandwidth "
        "memory, tighter supply discipline, and stronger pricing across data-center products. "
        "Management commentary suggested that customer orders were broadening beyond one large "
        "AI buyer, while inventory levels in consumer and industrial channels continued to "
        "normalize. Investors also focused on cash-flow recovery because capital expenditure "
        "plans are being balanced against expected margin expansion. "
    )
    return (paragraph * 3).strip()


class FakeStocktwitsPageClient:
    def __init__(self) -> None:
        self.pages: dict[tuple[str, str | None], StocktwitsPage] = {}
        self.calls: list[tuple[str, str | None, int]] = []

    def add_page(
        self,
        symbol: str,
        *,
        max_message_id: str | None,
        ids: list[int],
        cursor_more: bool | None = False,
        next_max_id: str | None = None,
    ) -> None:
        self.pages[(symbol.upper(), max_message_id)] = StocktwitsPage(
            messages=[_stocktwits_message(message_id, symbol) for message_id in ids],
            cursor_more=cursor_more,
            next_max_id=next_max_id,
        )

    def fetch_symbol_page(
        self,
        *,
        symbol: str,
        max_message_id: str | None = None,
        page_size: int = 30,
    ) -> StocktwitsPage:
        normalized = symbol.upper()
        self.calls.append((normalized, max_message_id, page_size))
        return self.pages.get((normalized, max_message_id)) or StocktwitsPage(
            messages=[],
            cursor_more=False,
        )


class FakeAsyncResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
        url: str = "https://example.test/",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.url = url


class FakeAsyncSession:
    def __init__(self, responses: dict[str, FakeAsyncResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def __aenter__(self) -> FakeAsyncSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool | None:
        return None

    async def get(self, url: str, **_: Any) -> FakeAsyncResponse:
        self.calls.append(url)
        return self.responses[url]


def _stocktwits_message(message_id: int, symbol: str) -> dict[str, object]:
    published_at = f"2030-01-01T12:{message_id % 60:02d}:00Z"
    return {
        "id": message_id,
        "body": f"{symbol} durable message {message_id}",
        "created_at": published_at,
        "user": {"id": "u1", "username": "tester"},
        "entities": {"sentiment": {"basic": "Bullish"}},
        "symbols": [{"symbol": symbol}],
    }


def test_default_sources_preserve_source_and_interface_dimensions() -> None:
    service = MonitoringBusService(InMemoryMonitoringRepository())

    sources = {source.source_id: source for source in service.list_sources()}

    assert sources["benzinga_news"].source_type is SourceType.MEDIA
    assert sources["benzinga_news"].interface_type is InterfaceType.BY_TICKER
    assert sources["benzinga_news"].poll_interval_seconds == 60
    assert sources["finnhub_company_news"].poll_interval_seconds == 60
    assert sources["stocktwits_messages"].source_type is SourceType.SOCIAL
    assert sources["stocktwits_messages"].interface_type is InterfaceType.BY_TICKER
    assert sources["stocktwits_messages"].poll_interval_seconds == 300
    assert sources["stocktwits_messages"].required_api_key_env is None
    assert sources["stocktwits_messages"].config["mode"] == "durable_polling"
    assert (
        sources["stocktwits_messages"].config["rapidapi_path"]
        == "/functions/v1/stocktwits-query"
    )
    assert sources["stocktwits_messages"].config["force_refresh"] is False
    assert sources["stocktwits_messages"].config["stagger_slots"] == 10
    assert sources["stocktwits_messages"].config["bootstrap_event_policy"] == "live_only"
    assert sources["tikhub_x_search"].interface_type is InterfaceType.BY_PARAMETER
    assert sources["tikhub_x_user_posts"].interface_type is InterfaceType.BY_PARAMETER
    assert sources["newswire_rss"].source_type is SourceType.MEDIA


def test_ingest_persists_raw_standard_event_and_blocks_exact_duplicate() -> None:
    repository = InMemoryMonitoringRepository()
    service = MonitoringBusService(repository)
    binding = service.configure_ticker_source(
        "AAPL",
        "benzinga_news",
        updated_by=UpdateActor.USER,
    )
    source = repository.get_source("benzinga_news")
    assert source is not None
    fetched = FetchedExternalMessage(
        source_id=source.source_id,
        binding_id=binding.binding_id,
        ticker=binding.ticker,
        source_type=source.source_type,
        interface_type=source.interface_type,
        provider_message_id="bz-1",
        source_url="https://example.test/news/1",
        source_published_at=binding.updated_at + timedelta(seconds=1),
        raw_payload={
            "id": "bz-1",
            "title": "Apple announces test event",
            "body": "Body",
            "url": "https://example.test/news/1",
            "stocks": [{"name": "AAPL"}],
            "created": (binding.updated_at + timedelta(seconds=1)).isoformat(),
        },
    )

    first = service.ingest_fetched(source=source, fetched=[fetched])
    second = service.ingest_fetched(source=source, fetched=[fetched])

    assert first.raw_inserted_count == 1
    assert first.standardized_count == 1
    assert first.event_count == 1
    assert second.raw_inserted_count == 0
    assert second.duplicate_count == 1
    assert second.event_count == 0
    assert len(repository.recent_standard_messages(ticker="AAPL")) == 1
    assert len(repository.recent_events(ticker="AAPL")) == 1
    raw = repository.recent_raw_messages(ticker="AAPL")[0]
    assert raw.duplicate_seen_count == 1


def test_benzinga_standard_body_is_plain_text_while_raw_keeps_html() -> None:
    repository = InMemoryMonitoringRepository()
    service = MonitoringBusService(repository)
    binding = service.configure_ticker_source("MU", "benzinga_news")
    source = repository.get_source("benzinga_news")
    assert source is not None
    html_body = (
        "<p>Micron&#8217;s setup includes <strong>HBM demand</strong> "
        '<a href="https://example.test">details</a>.</p><p>Second paragraph.</p>'
    )
    fetched = FetchedExternalMessage(
        source_id=source.source_id,
        binding_id=binding.binding_id,
        ticker=binding.ticker,
        source_type=source.source_type,
        interface_type=source.interface_type,
        provider_message_id="bz-html-1",
        source_url="https://example.test/bz-html",
        source_published_at=binding.updated_at + timedelta(seconds=1),
        raw_payload={
            "id": "bz-html-1",
            "title": "Micron HTML article",
            "body": html_body,
            "url": "https://example.test/bz-html",
            "stocks": [{"name": "MU"}],
            "created": (binding.updated_at + timedelta(seconds=1)).isoformat(),
        },
    )

    service.ingest_fetched(source=source, fetched=[fetched])

    raw = repository.recent_raw_messages(ticker="MU")[0]
    standard = repository.recent_standard_messages(ticker="MU")[0]
    assert raw.raw_payload["body"] == html_body
    assert "<p>" not in (standard.body or "")
    assert "<strong>" not in (standard.body or "")
    assert "Micron’s setup includes HBM demand details." in (standard.body or "")
    assert "Second paragraph." in (standard.body or "")


def test_deleted_binding_is_removed_from_live_stream_but_keeps_raw_audit() -> None:
    repository = InMemoryMonitoringRepository()
    service = MonitoringBusService(repository)
    binding = service.configure_ticker_source("AAPL", "benzinga_news")
    source = repository.get_source("benzinga_news")
    assert source is not None
    fetched = FetchedExternalMessage(
        source_id=source.source_id,
        binding_id=binding.binding_id,
        ticker=binding.ticker,
        source_type=source.source_type,
        interface_type=source.interface_type,
        provider_message_id="bz-delete-1",
        source_url="https://example.test/news/delete",
        source_published_at=binding.updated_at + timedelta(seconds=1),
        raw_payload={
            "id": "bz-delete-1",
            "title": "Apple delete audit event",
            "body": "Body",
            "url": "https://example.test/news/delete",
            "stocks": [{"name": "AAPL"}],
            "created": (binding.updated_at + timedelta(seconds=1)).isoformat(),
        },
    )

    service.ingest_fetched(source=source, fetched=[fetched])
    assert repository.recent_standard_messages(ticker="AAPL")
    assert service.delete_ticker_source("AAPL", "benzinga_news") is True

    assert repository.recent_raw_messages(ticker="AAPL")
    assert repository.recent_standard_messages(ticker="AAPL") == []
    assert repository.recent_events(ticker="AAPL") == []


def test_poll_state_tracks_last_cycle_counts_and_latency() -> None:
    repository = InMemoryMonitoringRepository()
    result = IngestBatchResult(
        source_id="benzinga_news",
        binding_id="AAPL:benzinga_news",
        ticker="AAPL",
        collected_count=5,
        historical_skipped_count=2,
        raw_inserted_count=3,
        standardized_count=3,
        event_count=3,
        latency_ms=456,
    )

    state = repository.record_poll_success(result)

    assert state.collected_count == 5
    assert state.last_collected_count == 5
    assert state.last_historical_skipped_count == 2
    assert state.last_event_count == 3
    assert state.last_latency_ms == 456


def test_ingest_skips_messages_published_before_binding_watermark() -> None:
    repository = InMemoryMonitoringRepository()
    service = MonitoringBusService(repository)
    binding = service.configure_ticker_source(
        "AAPL",
        "finnhub_company_news",
        updated_by=UpdateActor.AGENT,
    )
    source = repository.get_source("finnhub_company_news")
    assert source is not None

    result = service.ingest_fetched(
        source=source,
        fetched=[
            FetchedExternalMessage(
                source_id=source.source_id,
                binding_id=binding.binding_id,
                ticker=binding.ticker,
                source_type=source.source_type,
                interface_type=source.interface_type,
                provider_message_id="historic-1",
                source_url="https://example.test/historic",
                source_published_at=binding.updated_at - timedelta(seconds=1),
                raw_payload={
                    "id": "historic-1",
                    "headline": "Historic item",
                    "datetime": int((binding.updated_at - timedelta(seconds=1)).timestamp()),
                },
            )
        ],
    )

    assert result.collected_count == 1
    assert result.historical_skipped_count == 1
    assert result.raw_inserted_count == 0
    assert result.standardized_count == 0
    assert result.event_count == 0
    assert repository.recent_raw_messages(ticker="AAPL") == []
    assert repository.recent_events(ticker="AAPL") == []


async def test_media_enrichment_resolves_finnhub_redirect_and_updates_event_payload() -> None:
    repository = InMemoryMonitoringRepository()
    service = MonitoringBusService(repository)
    binding = service.configure_ticker_source(
        "MU",
        "finnhub_company_news",
        updated_by=UpdateActor.USER,
    )
    source = repository.get_source("finnhub_company_news")
    assert source is not None
    finnhub_url = "https://finnhub.io/api/news?id=101"
    article_url = "https://finance.yahoo.com/news/micron-ai-memory-demand-101.html"
    service.ingest_fetched(
        source=source,
        fetched=[
            FetchedExternalMessage(
                source_id=source.source_id,
                binding_id=binding.binding_id,
                ticker=binding.ticker,
                source_type=source.source_type,
                interface_type=source.interface_type,
                provider_message_id="101",
                source_url=finnhub_url,
                source_published_at=binding.updated_at + timedelta(seconds=1),
                raw_payload={
                    "id": 101,
                    "headline": "Micron rallies as AI memory demand improves",
                    "summary": "Micron shares rose in early trading.",
                    "url": finnhub_url,
                    "source": "Yahoo",
                    "datetime": int((binding.updated_at + timedelta(seconds=1)).timestamp()),
                },
            )
        ],
    )
    full_body = _long_article_body()
    fake_session = FakeAsyncSession(
        {
            finnhub_url: FakeAsyncResponse(
                status_code=302,
                headers={"Location": article_url},
                url=finnhub_url,
            ),
            article_url: FakeAsyncResponse(
                status_code=200,
                text="<article>Micron body</article>",
                url=article_url,
            ),
        }
    )

    payload = await service.enrich_recent_media_async(
        ticker="MU",
        limit=10,
        session_factory=lambda: fake_session,
        extractor=lambda _: full_body,
    )

    message = service.recent_messages(ticker="MU")[0]
    event = service.recent_events(ticker="MU")[0]
    assert payload["stats"]["succeeded_count"] == 1
    assert payload["stats"]["written_count"] == 1
    assert fake_session.calls == [finnhub_url, article_url]
    assert message.body == full_body
    assert message.url == article_url
    assert message.author == "finance.yahoo.com"
    assert message.metadata["media_enrichment"]["status"] == "success"
    assert event.payload["body"] == full_body
    assert event.payload["url"] == article_url


async def test_media_enrichment_rejects_short_extract_without_overwriting_body() -> None:
    repository = InMemoryMonitoringRepository()
    service = MonitoringBusService(repository)
    binding = service.configure_ticker_source(
        "MU",
        "finnhub_company_news",
        updated_by=UpdateActor.USER,
    )
    source = repository.get_source("finnhub_company_news")
    assert source is not None
    article_url = "https://www.cnbc.com/2026/06/26/micron-news.html"
    service.ingest_fetched(
        source=source,
        fetched=[
            FetchedExternalMessage(
                source_id=source.source_id,
                binding_id=binding.binding_id,
                ticker=binding.ticker,
                source_type=source.source_type,
                interface_type=source.interface_type,
                provider_message_id="102",
                source_url=article_url,
                source_published_at=binding.updated_at + timedelta(seconds=1),
                raw_payload={
                    "id": 102,
                    "headline": "Micron update",
                    "summary": "Existing summary.",
                    "url": article_url,
                    "source": "CNBC",
                    "datetime": int((binding.updated_at + timedelta(seconds=1)).timestamp()),
                },
            )
        ],
    )
    fake_session = FakeAsyncSession(
        {
            article_url: FakeAsyncResponse(
                status_code=200,
                text="<article>short</article>",
                url=article_url,
            ),
        }
    )

    payload = await service.enrich_recent_media_async(
        ticker="MU",
        limit=10,
        session_factory=lambda: fake_session,
        extractor=lambda _: "Still only a short summary.",
    )

    message = service.recent_messages(ticker="MU")[0]
    assert payload["stats"]["succeeded_count"] == 0
    assert payload["stats"]["written_count"] == 0
    assert payload["stats"]["failures_by_reason"] == {"incomplete_extract": 1}
    assert message.body == "Existing summary."
    assert message.metadata["media_enrichment"]["status"] == "failed"
    assert message.metadata["media_enrichment"]["reason"] == "incomplete_extract"


def test_sqlite_repository_persists_replayable_event_stream(tmp_path: Path) -> None:
    db_path = tmp_path / "monitoring.sqlite3"
    repository = SQLiteMonitoringRepository(db_path)
    service = MonitoringBusService(repository)
    binding = service.configure_ticker_source(
        "MSFT",
        "newswire_rss",
        parameters=MonitoringParameters(rss_urls=["https://example.test/rss.xml"]),
        updated_by=UpdateActor.USER,
    )
    source = repository.get_source("newswire_rss")
    assert source is not None
    service.ingest_fetched(
        source=source,
        fetched=[
            FetchedExternalMessage(
                source_id=source.source_id,
                binding_id=binding.binding_id,
                ticker=binding.ticker,
                source_type=source.source_type,
                interface_type=source.interface_type,
                provider_message_id="rss-guid-1",
                source_url="https://example.test/article",
                source_published_at=binding.updated_at + timedelta(seconds=1),
                raw_payload={
                    "guid": "rss-guid-1",
                    "title": "Newswire item",
                    "link": "https://example.test/article",
                    "description": "A durable item",
                    "published": (binding.updated_at + timedelta(seconds=1)).isoformat(),
                },
            )
        ],
    )

    reopened = SQLiteMonitoringRepository(db_path)
    events = reopened.recent_events(ticker="MSFT")

    assert len(events) == 1
    assert events[0].stream_offset == 1
    assert events[0].payload["ticker"] == "MSFT"
    marked = reopened.mark_event_consumed(events[0].event_id)
    assert marked is not None
    assert marked.consumed is True
    assert reopened.recent_events(ticker="MSFT")[0].consumed is True
    assert reopened.recent_standard_messages(ticker="MSFT")[0].title == "Newswire item"


def test_sqlite_defaults_merge_new_source_config_without_resetting_user_cadence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "monitoring.sqlite3"
    repository = SQLiteMonitoringRepository(db_path)
    source = repository.get_source("stocktwits_messages")
    assert source is not None
    old_config = {
        "mode": "rapidapi_or_public",
        "path_template": "/streams/symbol/{symbol}.json",
        "limit": 199,
        "force_refresh": True,
    }
    repository.upsert_source(
        source.model_copy(
            update={
                "poll_interval_seconds": 900,
                "config": old_config,
            },
            deep=True,
        )
    )

    reopened = SQLiteMonitoringRepository(db_path)
    migrated = reopened.get_source("stocktwits_messages")

    assert migrated is not None
    assert migrated.poll_interval_seconds == 900
    assert migrated.config["rapidapi_path"] == "/functions/v1/stocktwits-query"
    assert migrated.config["path_template"] == "/streams/symbol/{symbol}.json"
    assert migrated.config["limit"] == 100
    assert migrated.config["force_refresh"] is False
    assert migrated.config["timeout_seconds"] == 45
    assert migrated.config["mode"] == "durable_polling"
    assert migrated.config["bootstrap_event_policy"] == "live_only"


def test_stocktwits_durable_adapter_ingests_bus_events_and_poll_metadata() -> None:
    client = FakeStocktwitsPageClient()
    client.add_page("MU", max_message_id=None, ids=[103, 102], cursor_more=False)
    stocktwits_repository = InMemoryStocktwitsRepository()
    service = MonitoringBusService(
        InMemoryMonitoringRepository(),
        settings=_settings(),
        stocktwits_repository=stocktwits_repository,
        stocktwits_client=client,
    )
    binding = service.configure_ticker_source("MU", "stocktwits_messages")

    result = service.poll_binding("MU", "stocktwits_messages")
    state = stocktwits_repository.get_ticker_state("MU")
    poll_state = service.repository.list_poll_states(ticker="MU")[0]
    events = service.recent_events(ticker="MU")

    assert state is not None
    assert binding.binding_id == "MU:stocktwits_messages"
    assert result.collected_count == 2
    assert result.raw_inserted_count == 2
    assert result.event_count == 2
    assert result.metadata["coverage_status"] == "likely_complete"
    assert poll_state.metadata["coverage_status"] == "likely_complete"
    assert poll_state.metadata["checkpoint_found"] is False
    assert poll_state.metadata["stocktwits_run_id"]
    assert state.last_seen_message_id == "103"
    assert len(events) == 2
    assert events[0].source_id == "stocktwits_messages"


def test_stocktwits_durable_due_bindings_use_state_next_due_and_hot_mode() -> None:
    client = FakeStocktwitsPageClient()
    stocktwits_repository = InMemoryStocktwitsRepository()
    service = MonitoringBusService(
        InMemoryMonitoringRepository(),
        settings=_settings(),
        stocktwits_repository=stocktwits_repository,
        stocktwits_client=client,
    )
    binding = service.configure_ticker_source("MU", "stocktwits_messages")
    source = service.repository.get_source("stocktwits_messages")
    assert source is not None
    state = service._stocktwits().update_state(
        source=source,
        binding=binding,
        mode=TickerMode.HOT,
        hot_cadence_seconds=60,
        reset_schedule=True,
    )

    due = service.due_bindings(now=state.next_due_at)

    assert [item.binding_id for item in due] == [binding.binding_id]


def test_stocktwits_durable_new_tickers_are_staggered() -> None:
    stocktwits_repository = InMemoryStocktwitsRepository()
    service = MonitoringBusService(
        InMemoryMonitoringRepository(),
        settings=_settings(),
        stocktwits_repository=stocktwits_repository,
        stocktwits_client=FakeStocktwitsPageClient(),
    )

    service.configure_stocktwits_persistence("MU", reset_schedule=True)
    service.configure_stocktwits_persistence("AMD", reset_schedule=True)
    first = stocktwits_repository.get_ticker_state("MU")
    second = stocktwits_repository.get_ticker_state("AMD")

    assert first is not None
    assert second is not None
    delta_seconds = (second.next_due_at - first.next_due_at).total_seconds()
    assert 29 <= delta_seconds <= 31


def test_stocktwits_durable_ensure_state_is_read_only_when_unchanged() -> None:
    stocktwits_repository = InMemoryStocktwitsRepository()
    service = MonitoringBusService(
        InMemoryMonitoringRepository(),
        settings=_settings(),
        stocktwits_repository=stocktwits_repository,
        stocktwits_client=FakeStocktwitsPageClient(),
    )
    service.configure_stocktwits_persistence("MU", reset_schedule=True)
    source = service.repository.get_source("stocktwits_messages")
    binding = service.repository.get_binding("MU", "stocktwits_messages")
    before = stocktwits_repository.get_ticker_state("MU")

    assert source is not None
    assert binding is not None
    assert before is not None
    returned = service._stocktwits().ensure_state(source=source, binding=binding)
    after = stocktwits_repository.get_ticker_state("MU")

    assert after is not None
    assert returned.updated_at == before.updated_at
    assert after.updated_at == before.updated_at


def test_stocktwits_persistence_config_is_user_side_and_visible() -> None:
    client = FakeStocktwitsPageClient()
    service = MonitoringBusService(
        InMemoryMonitoringRepository(),
        settings=_settings(),
        stocktwits_repository=InMemoryStocktwitsRepository(),
        stocktwits_client=client,
    )

    output = service.configure_stocktwits_persistence(
        "MU",
        target_cadence_seconds=300,
        hot_cadence_seconds=60,
        page_size=20,
        max_pages_per_crawl=4,
        hot_message_threshold=50,
        hot_cooldown_successes=2,
        bootstrap_event_policy=BootstrapEventPolicy.SUPPRESS_INITIAL,
        reset_schedule=True,
        updated_reason="durable stocktwits smoke",
    )
    config = service.get_ticker_config("MU")
    stocktwits_item = config["by_ticker_sources"][0]

    assert output["stocktwits_state"]["page_size"] == 20
    assert output["stocktwits_state"]["bootstrap_event_policy"] == "suppress_initial"
    assert stocktwits_item["stocktwits_state"]["hot_cadence_seconds"] == 60
    assert "page_size" in stocktwits_item["user_only_fields"]
    assert "hot_message_threshold" in stocktwits_item["user_only_fields"]


def test_agent_tools_can_update_parameters_but_not_poll_interval() -> None:
    service = MonitoringBusService(InMemoryMonitoringRepository())
    tools = MonitoringToolClient(settings=_settings(), service=service)

    update = tools.for_tool("monitoring.update_ticker_config").call(
        _request(
            "monitoring.update_ticker_config",
            {
                "source_id": "tikhub_x_search",
                "search_terms": ["Apple product event"],
                "reason": "cover catalyst language",
            },
        )
    )
    denied = tools.for_tool("monitoring.update_ticker_config").call(
        _request(
            "monitoring.update_ticker_config",
            {"source_id": "benzinga_news", "poll_interval_seconds": 30},
        )
    )
    config = tools.for_tool("monitoring.get_ticker_config").call(
        _request("monitoring.get_ticker_config")
    )

    assert update.status is ResultStatus.SUCCEEDED
    assert denied.status is ResultStatus.FAILED
    assert denied.error is not None
    assert denied.error.code == "monitoring_permission_denied"
    assert config.status is ResultStatus.SUCCEEDED
    by_parameter = config.output["by_parameter_sources"]
    assert by_parameter[0]["binding"]["parameters"]["search_terms"] == [
        "Apple product event"
    ]
    assert by_parameter[0]["user_only_fields"] == [
        "poll_interval_seconds",
        "global_source_enabled",
    ]


def test_source_specific_parameter_schema_rejects_unsupported_fields() -> None:
    service = MonitoringBusService(InMemoryMonitoringRepository())

    try:
        service.configure_ticker_source(
            "AAPL",
            "finnhub_company_news",
            parameters=MonitoringParameters(search_terms=["Apple earnings"]),
        )
    except ValueError as exc:
        assert "ticker only" in str(exc)
    else:
        raise AssertionError("unsupported Finnhub parameters were accepted")

    try:
        service.configure_ticker_source(
            "AAPL",
            "tikhub_x_user_posts",
            parameters=MonitoringParameters(usernames=["one", "two", "three"]),
        )
    except ValueError as exc:
        assert "at most 2" in str(exc)
    else:
        raise AssertionError("too many TikHub usernames were accepted")


def test_user_only_poll_interval_guard_applies_at_service_layer() -> None:
    service = MonitoringBusService(InMemoryMonitoringRepository())

    try:
        service.set_source_poll_interval(
            "benzinga_news",
            seconds=120,
            updated_by=UpdateActor.AGENT,
        )
    except MonitoringPermissionError:
        pass
    else:
        raise AssertionError("agent update unexpectedly changed polling cadence")

    source = service.set_source_poll_interval(
        "benzinga_news",
        seconds=120,
        updated_by=UpdateActor.USER,
    )
    assert source.poll_interval_seconds == 120


def test_o2_agent_permissions_include_discovery_and_read_only_monitoring_tools() -> None:
    o2 = default_agent_registry().get(AgentName.O2_MONITORING_CONFIG)

    assert "anysearch.search" in o2.runtime.allowed_tools
    assert "tavily.search" in o2.runtime.allowed_tools
    assert "monitoring.get_ticker_config" in o2.runtime.allowed_tools
    assert "monitoring.update_ticker_config" not in o2.runtime.allowed_tools
    assert "monitoring.list_status" in o2.runtime.allowed_tools
    assert "monitoring.recent_events" in o2.runtime.allowed_tools


def test_benzinga_and_finnhub_collectors_build_documented_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/api/v2/news"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 101,
                        "title": "Benzinga headline",
                        "url": "https://example.test/bz",
                        "created": "Mon, 22 Jun 2026 09:30:00 -0400",
                    }
                ],
            )
        if request.url.path.endswith("/company-news"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 202,
                        "headline": "Finnhub headline",
                        "url": "https://example.test/fh",
                        "datetime": 1782133200,
                    }
                ],
            )
        return httpx.Response(404)

    registry = MonitoringCollectorRegistry(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    service = MonitoringBusService(InMemoryMonitoringRepository(), collectors=registry)
    benzinga_binding = service.configure_ticker_source("AAPL", "benzinga_news")
    finnhub_binding = service.configure_ticker_source("AAPL", "finnhub_company_news")
    sources = {source.source_id: source for source in service.list_sources()}

    benzinga = registry.collector_for(sources["benzinga_news"]).collect(
        source=sources["benzinga_news"],
        binding=benzinga_binding,
    )
    finnhub = registry.collector_for(sources["finnhub_company_news"]).collect(
        source=sources["finnhub_company_news"],
        binding=finnhub_binding,
    )

    assert benzinga[0].provider_message_id == "101"
    assert finnhub[0].provider_message_id == "202"
    assert requests[0].url.params["tickers"] == "AAPL"
    assert requests[0].url.params["token"] == "benzinga-key"
    assert requests[1].url.params["symbol"] == "AAPL"
    assert requests[1].url.params["token"] == "finnhub-key"


def test_benzinga_collector_can_fallback_to_documented_topics_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("tickers") == "MU":
            return httpx.Response(200, json=[])
        if request.url.params.get("topics") == "Micron":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 404,
                        "title": "Micron earnings setup",
                        "url": "https://example.test/bz-micron",
                        "created": "Wed, 24 Jun 2026 09:30:00 -0400",
                        "stocks": [],
                    }
                ],
            )
        return httpx.Response(404)

    registry = MonitoringCollectorRegistry(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    service = MonitoringBusService(InMemoryMonitoringRepository(), collectors=registry)
    binding = service.configure_ticker_source(
        "MU",
        "benzinga_news",
        parameters=MonitoringParameters(search_terms=["Micron"]),
    )
    source = service.repository.get_source("benzinga_news")
    assert source is not None

    messages = registry.collector_for(source).collect(source=source, binding=binding)

    assert messages[0].provider_message_id == "404"
    assert requests[0].url.params["tickers"] == "MU"
    assert requests[1].url.params["topics"] == "Micron"


def test_stocktwits_rapidapi_collector_uses_query_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "id": 303,
                        "created_at": "2026-06-23T10:00:00Z",
                        "body": "Micron HBM chatter",
                        "user": {"username": "tester"},
                        "symbols": [{"symbol": "MU"}],
                    }
                ]
            },
        )

    registry = MonitoringCollectorRegistry(
        _settings(stocktwits_rapidapi_host="stocktwits-sentiment-message-analytics-api.p.rapidapi.com"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    service = MonitoringBusService(InMemoryMonitoringRepository(), collectors=registry)
    binding = service.configure_ticker_source("MU", "stocktwits_messages")
    source = service.repository.get_source("stocktwits_messages")
    assert source is not None

    messages = registry.collector_for(source).collect(source=source, binding=binding)

    assert messages[0].provider_message_id == "303"
    assert requests[0].url.path == "/functions/v1/stocktwits-query"
    assert requests[0].url.params["action"] == "messages"
    assert requests[0].url.params["symbol"] == "MU"
    assert requests[0].url.params["primaryOnly"] == "true"
    assert requests[0].url.params["force_refresh"] == "false"
    assert requests[0].headers["x-rapidapi-host"] == (
        "stocktwits-sentiment-message-analytics-api.p.rapidapi.com"
    )
    assert requests[0].headers["x-rapidapi-key"] == "stocktwits-key"


def test_stocktwits_rapidapi_collector_fallback_waits_for_primary_retries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["x-rapidapi-key"] == "primary-key":
            return httpx.Response(429, json={"message": "quota exceeded"}, request=request)
        return httpx.Response(
            200,
            json={
                "messages": [
                    {
                        "id": 404,
                        "created_at": "2026-06-23T10:05:00Z",
                        "body": "Fallback key recovered Stocktwits messages",
                    }
                ]
            },
            request=request,
        )

    registry = MonitoringCollectorRegistry(
        _settings(
            stocktwits_rapidapi_key="primary-key",
            stocktwits_rapidapi_fallback_key="fallback-key",
            stocktwits_max_retries=3,
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    service = MonitoringBusService(InMemoryMonitoringRepository(), collectors=registry)
    binding = service.configure_ticker_source("MU", "stocktwits_messages")
    source = service.repository.get_source("stocktwits_messages")
    assert source is not None

    messages = registry.collector_for(source).collect(source=source, binding=binding)

    assert messages[0].provider_message_id == "404"
    assert [request.headers["x-rapidapi-key"] for request in requests] == [
        "primary-key",
        "primary-key",
        "primary-key",
        "fallback-key",
    ]


def test_tikhub_search_collector_skips_failed_term_when_another_term_succeeds() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("keyword") == "bad term":
            return httpx.Response(400, json={"detail": {"message": "invalid query"}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "timeline": [
                        {
                            "tweet_id": "777",
                            "created_at": "Fri Jun 26 12:00:00 +0000 2026",
                            "text": "Micron update",
                            "screen_name": "analyst",
                        }
                    ]
                }
            },
        )

    registry = MonitoringCollectorRegistry(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    service = MonitoringBusService(InMemoryMonitoringRepository(), collectors=registry)
    binding = service.configure_ticker_source(
        "MU",
        "tikhub_x_search",
        parameters=MonitoringParameters(search_terms=["bad term", "Micron"]),
    )
    source = service.repository.get_source("tikhub_x_search")
    assert source is not None

    messages = registry.collector_for(source).collect(source=source, binding=binding)

    assert [request.url.params["keyword"] for request in requests] == [
        "bad term",
        "Micron",
    ]
    assert len(messages) == 1
    assert messages[0].provider_message_id == "777"
    assert messages[0].metadata["search_term"] == "Micron"
