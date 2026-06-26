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


def test_default_sources_preserve_source_and_interface_dimensions() -> None:
    service = MonitoringBusService(InMemoryMonitoringRepository())

    sources = {source.source_id: source for source in service.list_sources()}

    assert sources["benzinga_news"].source_type is SourceType.MEDIA
    assert sources["benzinga_news"].interface_type is InterfaceType.BY_TICKER
    assert sources["benzinga_news"].poll_interval_seconds == 60
    assert sources["finnhub_company_news"].poll_interval_seconds == 60
    assert sources["stocktwits_messages"].source_type is SourceType.SOCIAL
    assert sources["stocktwits_messages"].interface_type is InterfaceType.BY_TICKER
    assert sources["stocktwits_messages"].poll_interval_seconds == 600
    assert (
        sources["stocktwits_messages"].config["rapidapi_path"]
        == "/functions/v1/stocktwits-query"
    )
    assert sources["stocktwits_messages"].config["force_refresh"] is False
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
