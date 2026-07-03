from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import (
    IngestBatchResult,
    InterfaceType,
    MonitoringParameters,
    SourceType,
    StandardMessage,
    UpdateActor,
)
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.schema import (
    RouteDecision,
    RuntimeExecutionRecord,
    RuntimeRoute,
    RuntimeSourceMessage,
)
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    DocumentBundle,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    UnifiedRuntimeSchedulerService,
)


def test_dashboard_real_message_bus_overview_messages_and_config() -> None:
    client, timestamp = _client_with_message_bus_state()
    query_date = timestamp.date().isoformat()

    overview = client.get(
        f"/api/dashboard/v1/tickers/NVDA/message-bus/overview?date={query_date}&tz=UTC"
    )

    assert overview.status_code == 200
    overview_payload = overview.json()["data"]
    assert overview_payload["ticker"] == "NVDA"
    assert overview_payload["today_raw_message_count"] == 1
    assert overview_payload["today_event_count"] == 1
    assert overview_payload["media_enrichment_success_rate"] == 1.0
    assert overview_payload["healthy_channel_count"] == 6
    assert overview_payload["total_channel_count"] == 6
    assert overview_payload["last_error_message"] is None

    messages = client.get(
        "/api/dashboard/v1/tickers/NVDA/message-bus/messages"
        "?source_id=benzinga_news&processing_status=w1_running&q=hyperscaler&sort=-collected_at"
    )

    assert messages.status_code == 200
    message_payload = messages.json()["data"]
    assert message_payload["page"] == {"limit": 50, "next_cursor": None, "has_more": False}
    assert message_payload["items"][0]["message_id"] == "std_nvda_message_bus"
    assert message_payload["items"][0]["source_label"] == "Benzinga News API"
    assert message_payload["items"][0]["processing_status"] == "w1_running"
    assert message_payload["items"][0]["runtime_execution_id"].startswith("pre_")

    config = client.get("/api/dashboard/v1/tickers/NVDA/message-bus/config")

    assert config.status_code == 200
    config_payload = config.json()["data"]
    assert config_payload["ticker"] == "NVDA"
    assert len(config_payload["sources"]) == 6
    sources = {source["source_id"]: source for source in config_payload["sources"]}
    assert sources["benzinga_news"]["enabled"] is True
    assert sources["benzinga_news"]["binding"]["parameters"]["search_terms"] == [
        "NVDA hyperscaler order"
    ]
    assert sources["benzinga_news"]["poll_state"]["status"] == "succeeded"
    assert sources["benzinga_news"]["poll_state"]["last_poll_new_message_count"] == 1
    assert sources["tikhub_x_search"]["enabled"] is False
    assert "search_terms" in sources["tikhub_x_search"]["agent_mutable_fields"]
    assert "target_cadence_seconds" in sources["stocktwits_messages"]["user_only_fields"]
    assert "tikhub_x_search" in config_payload["missing_source_ids"]

    events = client.get(
        "/api/dashboard/v1/events"
        "?ticker=NVDA&event_types=message_bus.message.created&once=true"
    )

    assert events.status_code == 200
    assert "text/event-stream" in events.headers["content-type"]
    assert "event: message_bus.message.created" in events.text
    assert '"standard_message_id": "std_nvda_message_bus"' in events.text


def test_dashboard_real_message_bus_config_mutations_and_errors() -> None:
    client, _timestamp = _client_with_message_bus_state()

    patched = client.patch(
        "/api/dashboard/v1/tickers/NVDA/message-bus/config/tikhub_x_search",
        json={
            "enabled": True,
            "search_terms": ["NVDA AI", "hyperscaler capex"],
            "reason": "unit test config",
        },
    )

    assert patched.status_code == 200
    patched_payload = patched.json()["data"]
    assert patched_payload["source_id"] == "tikhub_x_search"
    sources = {source["source_id"]: source for source in patched_payload["sources"]}
    assert sources["tikhub_x_search"]["enabled"] is True
    assert sources["tikhub_x_search"]["binding"]["parameters"]["search_terms"] == [
        "NVDA AI",
        "hyperscaler capex",
    ]

    disabled = client.patch(
        "/api/dashboard/v1/tickers/NVDA/message-bus/config/tikhub_x_search",
        json={"enabled": False, "reason": "pause channel"},
    )

    assert disabled.status_code == 200
    disabled_sources = {
        source["source_id"]: source for source in disabled.json()["data"]["sources"]
    }
    assert disabled_sources["tikhub_x_search"]["enabled"] is False
    assert disabled_sources["tikhub_x_search"]["binding"]["parameters"]["search_terms"] == [
        "NVDA AI",
        "hyperscaler capex",
    ]

    invalid_field = client.patch(
        "/api/dashboard/v1/tickers/NVDA/message-bus/config/tikhub_x_search",
        json={"poll_interval_seconds": 30},
    )
    assert invalid_field.status_code == 422
    assert invalid_field.json()["error"]["code"] == "INVALID_PARAMS"

    too_many_terms = client.patch(
        "/api/dashboard/v1/tickers/NVDA/message-bus/config/tikhub_x_search",
        json={"search_terms": ["one", "two", "three", "four"]},
    )
    assert too_many_terms.status_code == 422
    assert too_many_terms.json()["error"]["code"] == "INVALID_PARAMS"

    deleted = client.delete(
        "/api/dashboard/v1/tickers/NVDA/message-bus/config/tikhub_x_search"
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {
        "ticker": "NVDA",
        "source_id": "tikhub_x_search",
        "removed": True,
    }

    missing_source = client.delete(
        "/api/dashboard/v1/tickers/NVDA/message-bus/config/not_a_source"
    )
    assert missing_source.status_code == 404
    assert missing_source.json()["error"]["code"] == "NOT_FOUND"


class _DocumentProvider:
    def latest(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return self._bundle(ticker)

    def initialize(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        return self._bundle(ticker)

    def _bundle(self, ticker: str) -> DocumentBundle:
        return DocumentBundle(
            status=DocumentSetStatus(
                ticker=ticker,
                checked_at=datetime.now(UTC),
                usable=False,
            )
        )


def _client_with_message_bus_state() -> tuple[TestClient, datetime]:
    monitoring_service = MonitoringBusService(InMemoryMonitoringRepository())
    runtime_service = PersistentRuntimeExecutionService(InMemoryPersistentRuntimeRepository())
    timestamp = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
    _seed_message_bus(monitoring_service, runtime_service, timestamp)
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=_DocumentProvider(),
        monitoring_service=monitoring_service,
        runtime_service=runtime_service,
    )
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
        )
    )
    return client, timestamp


def _seed_message_bus(
    monitoring_service: MonitoringBusService,
    runtime_service: PersistentRuntimeExecutionService,
    timestamp: datetime,
) -> None:
    binding = monitoring_service.configure_ticker_source(
        "NVDA",
        "benzinga_news",
        parameters=MonitoringParameters(search_terms=["NVDA hyperscaler order"]),
        enabled=True,
        updated_by=UpdateActor.USER,
        updated_reason="unit test seed",
        merge=False,
    )
    message = StandardMessage(
        standard_message_id="std_nvda_message_bus",
        raw_message_id="raw_nvda_message_bus",
        source_id="benzinga_news",
        binding_id=binding.binding_id,
        ticker="NVDA",
        source_type=SourceType.MEDIA,
        interface_type=InterfaceType.BY_TICKER,
        title="NVDA hyperscaler order update",
        body="Hyperscaler order update mentions NVDA AI server demand.",
        url="https://example.test/nvda",
        symbols=["NVDA"],
        published_at=timestamp,
        collected_at=timestamp,
        normalized_at=timestamp,
        metadata={
            "summary": "Hyperscaler order update mentions NVDA.",
            "media_enrichment": {"succeeded": True},
        },
    )
    monitoring_service.repository.save_standard_message(message)
    monitoring_service.repository.append_event(message)
    monitoring_service.repository.record_poll_success(
        IngestBatchResult(
            source_id="benzinga_news",
            binding_id=binding.binding_id,
            ticker="NVDA",
            collected_count=1,
            raw_inserted_count=1,
            standardized_count=1,
            event_count=1,
            latency_ms=1200,
        )
    )
    source_message = RuntimeSourceMessage(
        source_message_id=message.standard_message_id,
        raw_message_id=message.raw_message_id,
        ticker="NVDA",
        source_type=SourceType.MEDIA,
        source_id=message.source_id,
        title=message.title,
        body=message.body,
        url=message.url,
        symbols=["NVDA"],
        collected_at=timestamp,
    )
    runtime_service.repository.save_execution(
        RuntimeExecutionRecord(
            source_message=source_message,
            route_decision=RouteDecision(
                source_message_id=source_message.source_message_id,
                ticker="NVDA",
                route=RuntimeRoute.ARCHIVE,
                reason="unit test route",
            ),
            message_statuses=["received", "w1_running"],
            created_at=timestamp,
        )
    )
