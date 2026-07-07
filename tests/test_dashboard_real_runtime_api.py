from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import (
    InterfaceType,
    MonitoringParameters,
    SourceType,
    StandardMessage,
    UpdateActor,
)
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.schema import (
    A2Result,
    A2VerificationStatus,
    Conviction,
    ExecutionExceptionLog,
    RouteDecision,
    RuntimeExecutionRecord,
    RuntimeNodeTrace,
    RuntimeRoute,
    RuntimeSourceMessage,
    SizeBucket,
    TradeIntent,
    TradeRecordStatus,
    TradeSide,
    TradingRecord,
    W1Confidence,
    W1NoveltyLabel,
    W1Result,
    W2Result,
    W2Type,
)
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    DocumentBundle,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    UnifiedRuntimeSchedulerService,
)


def test_dashboard_real_runtime_overview_graph_nodes_and_sse() -> None:
    client, timestamp = _client_with_runtime_state()
    query_date = timestamp.date().isoformat()

    overview = client.get(
        f"/api/dashboard/v1/tickers/NVDA/runtime/overview?date={query_date}&tz=UTC"
    )

    assert overview.status_code == 200
    overview_payload = overview.json()["data"]
    assert overview_payload["ticker"] == "NVDA"
    assert overview_payload["queue_message_count"] == 1
    assert overview_payload["w1_today_count"] == 3
    assert overview_payload["w2_today_count"] == 3
    assert overview_payload["o3_today_count"] == 2
    assert overview_payload["dtc_today_count"] == 1
    assert overview_payload["eba_today_count"] == 1
    assert overview_payload["failed_task_count"] == 1
    assert overview_payload["w1_avg_latency_ms"] == 1100
    assert overview_payload["avg_processing_latency_ms"] is not None

    graph = client.get("/api/dashboard/v1/tickers/NVDA/runtime/graph")

    assert graph.status_code == 200
    graph_payload = graph.json()["data"]
    nodes = {node["node_id"]: node for node in graph_payload["nodes"]}
    edges = {edge["edge_id"]: edge for edge in graph_payload["edges"]}
    assert nodes["w1"]["in_count"] == 3
    assert nodes["a2"]["in_count"] == 1
    assert nodes["a2"]["out_count"] == 1
    assert nodes["o3"]["status"] == "degraded"
    assert nodes["exception_queue"]["in_count"] == 1
    assert nodes["trading_records"]["in_count"] == 1
    assert edges["route_engine_to_trading"]["count"] == 1
    assert edges["route_engine_to_o3"]["count"] == 1
    assert edges["route_engine_to_a2"]["count"] == 1
    assert edges["a2_to_o3"]["count"] == 1
    assert edges["route_engine_to_exception_queue"]["count"] == 1
    assert edges["o3_to_exception_queue"]["count"] == 1

    node = client.get(
        f"/api/dashboard/v1/tickers/NVDA/runtime/nodes/w2"
        f"?date={query_date}&tz=UTC&limit=1"
    )

    assert node.status_code == 200
    node_payload = node.json()["data"]
    assert node_payload["node"]["node_id"] == "w2"
    assert node_payload["node"]["today_count"] == 3
    assert node_payload["page"] == {
        "limit": 1,
        "next_cursor": "cur_1",
        "has_more": True,
        "total_count": 3,
    }
    assert node_payload["recent_records"][0]["execution_id"] == "pre_nvda_001"
    assert "Direct Trade Candidate" in node_payload["recent_records"][0]["output_summary"]

    missing_node = client.get("/api/dashboard/v1/tickers/NVDA/runtime/nodes/not_a_node")
    assert missing_node.status_code == 404
    assert missing_node.json()["error"]["code"] == "NOT_FOUND"

    events = client.get(
        "/api/dashboard/v1/events"
        "?ticker=NVDA&event_types=runtime.execution.failed&once=true"
    )

    assert events.status_code == 200
    assert "event: runtime.execution.failed" in events.text
    assert '"execution_id": "pre_nvda_003"' in events.text


def test_dashboard_real_runtime_execution_list_filters_pagination_and_detail() -> None:
    client, _timestamp = _client_with_runtime_state()

    page = client.get("/api/dashboard/v1/tickers/NVDA/runtime/executions?limit=2")

    assert page.status_code == 200
    page_payload = page.json()["data"]
    assert [item["execution_id"] for item in page_payload["items"]] == [
        "pre_nvda_001",
        "pre_nvda_002",
    ]
    assert page_payload["page"] == {
        "limit": 2,
        "next_cursor": "cur_2",
        "has_more": True,
        "total_count": 3,
    }

    filtered = client.get(
        "/api/dashboard/v1/tickers/NVDA/runtime/executions"
        "?route=trading_record&status=completed&source_type=media&limit=1"
    )

    assert filtered.status_code == 200
    filtered_items = filtered.json()["data"]["items"]
    assert len(filtered_items) == 1
    assert filtered_items[0]["execution_id"] == "pre_nvda_001"
    assert filtered_items[0]["message_title"] == "NVDA dashboard runtime order update"
    assert filtered_items[0]["final_route"] == "trading_record"
    assert filtered_items[0]["node_durations_ms"] == {"W1": 1100, "W2": 1300}
    assert filtered_items[0]["exception_types"] == []

    detail = client.get("/api/dashboard/v1/tickers/NVDA/runtime/executions/pre_nvda_001")

    assert detail.status_code == 200
    detail_payload = detail.json()["data"]
    assert detail_payload["execution_id"] == "pre_nvda_001"
    assert detail_payload["source_message"]["title"] == "NVDA dashboard runtime order update"
    assert detail_payload["route_decision"]["final_route"] == "trading_record"
    assert detail_payload["w1_result"]["novelty_label"] == "material_update"
    assert detail_payload["exceptions"] == []

    missing = client.get("/api/dashboard/v1/tickers/NVDA/runtime/executions/pre_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_dashboard_real_runtime_result_records_filter_pagination() -> None:
    client, _timestamp = _client_with_runtime_state()

    page = client.get("/api/dashboard/v1/tickers/NVDA/runtime/records?limit=2")

    assert page.status_code == 200
    page_payload = page.json()["data"]
    assert [item["record_id"] for item in page_payload["items"]] == [
        "pre_nvda_001",
        "pre_nvda_002",
    ]
    assert page_payload["items"][0]["result_type"] == "trading_record"
    assert page_payload["items"][0]["node_durations_ms"] == {"W1": 1100, "W2": 1300}
    assert page_payload["items"][0]["is_new"] is True
    assert page_payload["items"][0]["policy_type"] == "Direct Trade Candidate"
    assert page_payload["items"][0]["summary"] == "NVDA dashboard runtime order update."
    assert page_payload["items"][0]["result"]["side"] == "long"
    assert page_payload["items"][0]["result"]["conviction"] == "high"
    assert page_payload["items"][0]["result"]["size_bucket"] == "normal"
    assert page_payload["items"][0]["result"]["matched_policy_code"] == "POLICY_DTC_ORDER"
    assert page_payload["items"][0]["result"]["trade_intent.reasoning"] == (
        "Unit test trade intent."
    )
    assert page_payload["page"]["next_cursor"] == "cur_2"
    assert page_payload["page"]["has_more"] is True

    trading = client.get(
        "/api/dashboard/v1/tickers/NVDA/runtime/records"
        "?result_type=trading_record&limit=10"
    )

    assert trading.status_code == 200
    trading_items = trading.json()["data"]["items"]
    assert len(trading_items) == 1
    assert trading_items[0]["execution_id"] == "pre_nvda_001"
    assert trading_items[0]["result_type"] == "trading_record"

    exceptions = client.get(
        "/api/dashboard/v1/tickers/NVDA/runtime/records"
        "?result_type=exception_queue&limit=10"
    )

    assert exceptions.status_code == 200
    exception_items = exceptions.json()["data"]["items"]
    assert len(exception_items) == 1
    assert exception_items[0]["record_id"] == "exc_nvda_001"
    assert exception_items[0]["result"] == {
        "exception_type": "RuntimeWorkerTimeout",
        "node": "O3",
        "message": "O3 worker exceeded the bounded runtime.",
    }

    invalid = client.get(
        "/api/dashboard/v1/tickers/NVDA/runtime/records?result_type=not_a_result"
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_PARAMS"


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


def _client_with_runtime_state() -> tuple[TestClient, datetime]:
    monitoring_service = MonitoringBusService(InMemoryMonitoringRepository())
    runtime_service = PersistentRuntimeExecutionService(InMemoryPersistentRuntimeRepository())
    timestamp = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
    _seed_runtime_state(monitoring_service, runtime_service, timestamp)
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


def _seed_runtime_state(
    monitoring_service: MonitoringBusService,
    runtime_service: PersistentRuntimeExecutionService,
    timestamp: datetime,
) -> None:
    binding = monitoring_service.configure_ticker_source(
        "NVDA",
        "benzinga_news",
        parameters=MonitoringParameters(search_terms=["NVDA runtime"]),
        enabled=True,
        updated_by=UpdateActor.USER,
        updated_reason="unit test runtime seed",
        merge=False,
    )
    _save_standard_message(
        monitoring_service,
        "std_nvda_runtime_001",
        title="NVDA dashboard runtime order update",
        binding_id=binding.binding_id,
        timestamp=timestamp,
    )
    pending = _save_standard_message(
        monitoring_service,
        "std_nvda_pending_runtime",
        title="NVDA pending runtime queue message",
        binding_id=binding.binding_id,
        timestamp=timestamp,
    )
    monitoring_service.repository.append_event(pending)

    runtime_service.repository.save_execution(
        RuntimeExecutionRecord(
            execution_id="pre_nvda_001",
            source_message=_source_message(
                "std_nvda_runtime_001",
                source_type=SourceType.MEDIA,
                title=None,
                timestamp=timestamp,
            ),
            route_decision=RouteDecision(
                source_message_id="std_nvda_runtime_001",
                ticker="NVDA",
                route=RuntimeRoute.TRADING_RECORD,
                reason="DTC policy matched.",
            ),
            w1_result=W1Result(
                is_new=True,
                novelty_label=W1NoveltyLabel.MATERIAL_UPDATE,
                confidence=W1Confidence.HIGH,
                reasoning="Material order update.",
            ),
            w2_result=W2Result(
                type=W2Type.DIRECT_TRADE_CANDIDATE,
                matched_policy_code="POLICY_DTC_ORDER",
                reasoning="Matches DTC policy.",
            ),
            status="completed",
            message_statuses=["received", "workers_completed", "routed_to_trading_records"],
            node_traces=[
                RuntimeNodeTrace(
                    node="W1",
                    status="succeeded",
                    duration_ms=1100,
                    started_at=timestamp,
                ),
                RuntimeNodeTrace(
                    node="W2",
                    status="succeeded",
                    duration_ms=1300,
                    started_at=timestamp,
                ),
            ],
            created_at=timestamp,
        )
    )
    runtime_service.repository.save_trading_record(
        TradingRecord(
            source_message_id="std_nvda_runtime_001",
            ticker="NVDA",
            source_type=SourceType.MEDIA,
            route="trading_record",
            matched_policy_code="POLICY_DTC_ORDER",
            trade_intent=TradeIntent(
                side=TradeSide.LONG,
                conviction=Conviction.HIGH,
                size_bucket=SizeBucket.NORMAL,
                reasoning="Unit test trade intent.",
            ),
            status=TradeRecordStatus.RECORDED_ONLY,
            created_at=timestamp,
        )
    )

    second_time = timestamp - timedelta(minutes=1)
    runtime_service.repository.save_execution(
        RuntimeExecutionRecord(
            execution_id="pre_nvda_002",
            source_message=_source_message(
                "std_nvda_runtime_002",
                source_type=SourceType.SOCIAL,
                title="NVDA social rumor needs O3",
                timestamp=second_time,
            ),
            route_decision=RouteDecision(
                source_message_id="std_nvda_runtime_002",
                ticker="NVDA",
                route=RuntimeRoute.A2,
                reason="Verify social rumor before O3 duty expert.",
            ),
            w1_result=W1Result(
                is_new=True,
                novelty_label=W1NoveltyLabel.NEW_EVENT,
                confidence=W1Confidence.MEDIUM,
                reasoning="Potential new customer rumor.",
            ),
            w2_result=W2Result(
                type=W2Type.ESCALATE_TO_BACKGROUND_AGENT,
                matched_policy_code="POLICY_EBA_RUMOR",
                reasoning="Needs expert verification.",
            ),
            a2_result=A2Result(
                is_new=True,
                verification_status=A2VerificationStatus.LIKELY_TRUE,
                reasoning="A2 found enough corroboration for O3 escalation.",
            ),
            status="running",
            message_statuses=[
                "received",
                "w1_completed",
                "w2_completed",
                "a2_running",
                "o3_running",
            ],
            node_traces=[
                RuntimeNodeTrace(
                    node="W1",
                    status="succeeded",
                    duration_ms=1100,
                    started_at=second_time,
                ),
                RuntimeNodeTrace(
                    node="W2",
                    status="succeeded",
                    duration_ms=1500,
                    started_at=second_time,
                ),
                RuntimeNodeTrace(
                    node="A2",
                    status="succeeded",
                    duration_ms=2100,
                    started_at=second_time,
                ),
                RuntimeNodeTrace(
                    node="O3",
                    status="running",
                    duration_ms=48000,
                    started_at=second_time,
                ),
            ],
            created_at=second_time,
        )
    )

    failed_time = timestamp - timedelta(minutes=2)
    runtime_service.repository.save_execution(
        RuntimeExecutionRecord(
            execution_id="pre_nvda_003",
            source_message=_source_message(
                "std_nvda_runtime_003",
                source_type=SourceType.MEDIA,
                title="NVDA malformed runtime worker payload",
                timestamp=failed_time,
            ),
            route_decision=RouteDecision(
                source_message_id="std_nvda_runtime_003",
                ticker="NVDA",
                route=RuntimeRoute.FAILED_WITH_EXCEPTION,
                reason="O3 worker timed out.",
            ),
            w1_result=W1Result(
                is_new=True,
                novelty_label=W1NoveltyLabel.MATERIAL_UPDATE,
                confidence=W1Confidence.MEDIUM,
                reasoning="New but failed downstream.",
            ),
            w2_result=W2Result(
                type=W2Type.NULL,
                reasoning="No direct policy matched.",
            ),
            status="failed",
            message_statuses=["received", "workers_completed", "failed_with_exception"],
            node_traces=[
                RuntimeNodeTrace(
                    node="W1",
                    status="succeeded",
                    duration_ms=1100,
                    started_at=failed_time,
                ),
                RuntimeNodeTrace(
                    node="W2",
                    status="succeeded",
                    duration_ms=1200,
                    started_at=failed_time,
                ),
                RuntimeNodeTrace(
                    node="O3",
                    status="failed",
                    duration_ms=90000,
                    started_at=failed_time,
                ),
            ],
            exception_ids=["exc_nvda_001"],
            created_at=failed_time,
        )
    )
    runtime_service.repository.save_exception(
        ExecutionExceptionLog(
            exception_id="exc_nvda_001",
            source_message_id="std_nvda_runtime_003",
            ticker="NVDA",
            node="O3",
            exception_type="RuntimeWorkerTimeout",
            message="O3 worker exceeded the bounded runtime.",
            created_at=failed_time,
        )
    )


def _save_standard_message(
    monitoring_service: MonitoringBusService,
    standard_message_id: str,
    *,
    title: str,
    binding_id: str,
    timestamp: datetime,
) -> StandardMessage:
    return monitoring_service.repository.save_standard_message(
        StandardMessage(
            standard_message_id=standard_message_id,
            raw_message_id=f"raw_{standard_message_id}",
            source_id="benzinga_news",
            binding_id=binding_id,
            ticker="NVDA",
            source_type=SourceType.MEDIA,
            interface_type=InterfaceType.BY_TICKER,
            title=title,
            body=f"{title}.",
            symbols=["NVDA"],
            published_at=timestamp,
            collected_at=timestamp,
            normalized_at=timestamp,
        )
    )


def _source_message(
    source_message_id: str,
    *,
    source_type: SourceType,
    title: str | None,
    timestamp: datetime,
) -> RuntimeSourceMessage:
    return RuntimeSourceMessage(
        source_message_id=source_message_id,
        raw_message_id=f"raw_{source_message_id}",
        ticker="NVDA",
        source_type=source_type,
        source_id="benzinga_news" if source_type is SourceType.MEDIA else "tikhub_x_search",
        title=title,
        body=f"{title or source_message_id}.",
        symbols=["NVDA"],
        collected_at=timestamp,
    )
