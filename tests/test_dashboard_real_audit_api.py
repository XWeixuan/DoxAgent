from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
from doxagent.dashboard_api.real_service import RealDashboardOverviewService
from doxagent.model_usage import (
    InMemoryModelUsageRepository,
    ModelPricingCatalog,
    ModelUsageCostService,
    ModelUsageEvent,
)
from doxagent.model_usage.pricing import DEFAULT_PRICING_PATH
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import SourceType
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.schema import (
    Conviction,
    RouteDecision,
    RuntimeExecutionRecord,
    RuntimeNodeTrace,
    RuntimeRoute,
    RuntimeSourceMessage,
    SizeBucket,
    TradeAuditSnapshot,
    TradeDecisionSource,
    TradeIntent,
    TradeRecordStatus,
    TradeSide,
    TradingRecord,
)
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.revenue_audit import (
    InMemoryRevenueAuditRepository,
    MinuteBar,
    RevenueAuditConfig,
    RevenueAuditService,
)
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    DocumentBundle,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    UnifiedRuntimeSchedulerService,
)


def test_dashboard_real_revenue_audit_calculates_and_exposes_bounded_views() -> None:
    client, timestamp = _client_with_audit_state()
    query_date = timestamp.date().isoformat()

    response = client.get(
        f"/api/dashboard/v1/tickers/NVDA/audit/revenue"
        f"?period=7d&date={query_date}&tz=UTC"
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["ticker"] == "NVDA"
    assert payload["period"] == "7d"
    assert payload["basis"] == "system_executable"
    assert payload["status"] == "not_started"
    assert payload["trade_intent_count"] == 2
    assert payload["auditable_trade_count"] == 2
    assert payload["audited_trade_count"] == 0
    assert payload["coverage_rate"] == 0
    assert payload["simulated_pnl_usd"] is None

    run_response = client.post(
        "/api/dashboard/v1/tickers/NVDA/audit/revenue/run",
        json={"date": query_date, "tz": "UTC"},
    )

    assert run_response.status_code == 200
    run_payload = run_response.json()["data"]
    assert run_payload["ticker"] == "NVDA"
    assert run_payload["date"] == query_date
    assert run_payload["status"] == "completed"
    assert run_payload["audit_run_id"].startswith("revaud_")
    assert run_payload["audited_count"] == 3

    overview = client.get(
        f"/api/dashboard/v1/tickers/NVDA/audit/revenue"
        f"?period=7d&date={query_date}&tz=UTC&basis=system_executable"
    ).json()["data"]
    assert overview["status"] == "partial"
    assert overview["audited_trade_count"] == 1
    assert overview["coverage_rate"] == 0.5
    assert overview["simulated_pnl_usd"] is not None
    assert overview["latency_losses"]["capture_loss"]["matched_trade_count"] == 1

    trend = client.get(
        f"/api/dashboard/v1/tickers/NVDA/audit/revenue/trend"
        f"?period=7d&date={query_date}&tz=UTC&basis=system_executable"
    )
    assert trend.status_code == 200
    assert len(trend.json()["data"]["items"]) == 7

    records = client.get(
        f"/api/dashboard/v1/tickers/NVDA/audit/revenue/records"
        f"?period=7d&date={query_date}&tz=UTC&basis=system_executable&limit=1"
    )
    assert records.status_code == 200
    records_payload = records.json()["data"]
    assert len(records_payload["items"]) == 1
    assert records_payload["items"][0]["status"] == "audited"
    assert records_payload["items"][0]["data_source"] == "fixture:1m"

    detail = client.get(
        "/api/dashboard/v1/tickers/NVDA/audit/revenue/records/trd_nvda_today"
    )
    assert detail.status_code == 200
    assert len(detail.json()["data"]["results"]) == 3

    events = client.get(
        "/api/dashboard/v1/events"
        "?ticker=NVDA&event_types=audit.revenue.status_changed&once=true"
    )

    assert events.status_code == 200
    assert "event: audit.revenue.status_changed" in events.text
    assert '"status": "completed"' in events.text
    assert "paper-trade-v1" in events.text


def test_dashboard_real_cost_audit_extracts_model_usage_details_without_pricing_guess() -> None:
    client, timestamp = _client_with_audit_state()
    query_date = timestamp.date().isoformat()

    response = client.get(
        f"/api/dashboard/v1/tickers/NVDA/audit/cost"
        f"?period=7d&group_by=node&date={query_date}&tz=UTC"
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["ticker"] == "NVDA"
    assert payload["period"] == "7d"
    assert payload["group_by"] == "node"
    assert payload["status"] == "partial"
    assert payload["kpis"]["today_input_tokens"] == 1800
    assert payload["kpis"]["today_output_tokens"] == 600
    assert payload["kpis"]["today_total_tokens"] == 2400
    assert payload["kpis"]["today_total_cost_usd"] == 0.012
    assert payload["kpis"]["highest_cost_node"] == "O3"
    assert payload["kpis"]["retry_cost_usd"] == 0.012
    assert len(payload["trend"]) == 7
    assert payload["trend"][-1]["total_tokens"] == 2400
    assert payload["trend"][-1]["total_cost_usd"] == 0.012
    by_node = {item["key"]: item for item in payload["breakdown"]["by_node"]}
    assert by_node["O3"]["cost_usd"] == 0.012
    assert by_node["W1"]["cost_usd"] is None
    assert by_node["W1"]["total_tokens"] == 1200

    details = client.get(
        "/api/dashboard/v1/tickers/NVDA/audit/cost/details"
        "?node=O3&status=retried&limit=1"
    )

    assert details.status_code == 200
    detail_payload = details.json()["data"]
    assert len(detail_payload["items"]) == 1
    assert detail_payload["items"][0]["node"] == "O3"
    assert detail_payload["items"][0]["model"] == "gpt-4.1"
    assert detail_payload["items"][0]["is_retry"] is True
    assert detail_payload["items"][0]["cost_usd"] == 0.012
    assert detail_payload["page"] == {
        "limit": 1,
        "next_cursor": None,
        "has_more": False,
        "total_count": 1,
    }

def test_dashboard_real_cost_audit_uses_persisted_model_usage_as_primary_source() -> None:
    timestamp = datetime(2026, 6, 30, 15, 30, tzinfo=UTC)
    repository = InMemoryModelUsageRepository(
        [
            ModelUsageEvent(
                provider="bailian",
                model="qwen3.7-max",
                status="retried",
                input_tokens=1_000_000,
                output_tokens=500_000,
                total_tokens=1_500_000,
                retry_count=1,
                ticker="NVDA",
                runtime_node="O3",
                workflow_node="persistent_runtime_execution",
                agent_name="O3",
                task_type="runtime_o3_judgment",
                source_message_id="std_nvda_usage_today",
                created_at=timestamp,
            ),
            ModelUsageEvent(
                provider="bailian",
                model="unknown-bailian-model",
                status="succeeded",
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                ticker="NVDA",
                runtime_node="W1",
                source_message_id="std_nvda_usage_missing_price",
                created_at=timestamp - timedelta(days=2),
            ),
            ModelUsageEvent(
                provider="bailian",
                model="qwen3.7-max",
                status="succeeded",
                input_tokens=2_000_000,
                output_tokens=1_000_000,
                total_tokens=3_000_000,
                ticker="NVDA",
                runtime_node="W2",
                source_message_id="std_nvda_usage_old",
                created_at=timestamp - timedelta(days=9),
            ),
        ]
    )
    service = ModelUsageCostService(repository, pricing_catalog=_default_model_pricing_catalog())
    client, _ = _client_with_audit_state(model_usage_service=service)
    query_date = timestamp.date().isoformat()

    response = client.get(
        f"/api/dashboard/v1/tickers/NVDA/audit/cost"
        f"?period=7d&group_by=node&date={query_date}&tz=UTC"
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "partial"
    assert payload["kpis"]["today_input_tokens"] == 1_000_100
    assert payload["kpis"]["today_output_tokens"] == 500_050
    assert payload["kpis"]["today_total_tokens"] == 1_500_150
    assert payload["kpis"]["today_total_cost_usd"] == pytest.approx(round(13.5 / 6.8, 6))
    assert payload["kpis"]["highest_cost_node"] == "O3"
    assert payload["kpis"]["retry_cost_usd"] == pytest.approx(round(13.5 / 6.8, 6))
    assert payload["trend"][-1]["total_tokens"] == 1_500_000
    by_node = {item["key"]: item for item in payload["breakdown"]["by_node"]}
    assert by_node["O3"]["cost_usd"] == pytest.approx(round(13.5 / 6.8, 6))
    assert by_node["W1"]["cost_usd"] is None

    details = client.get(
        "/api/dashboard/v1/tickers/NVDA/audit/cost/details"
        "?period=7d&node=O3&status=retried&limit=1&date=2026-06-30&tz=UTC"
    )

    assert details.status_code == 200
    detail_payload = details.json()["data"]
    assert detail_payload["page"]["total_count"] == 1
    assert detail_payload["items"][0]["model"] == "qwen3.7-max"
    assert detail_payload["items"][0]["pricing_status"] == "priced"
    assert detail_payload["items"][0]["source_ref"]["source_message_id"] == (
        "std_nvda_usage_today"
    )


def test_dashboard_real_audit_rejects_invalid_period_and_group_by() -> None:
    client, _timestamp = _client_with_audit_state()

    bad_period = client.get("/api/dashboard/v1/tickers/NVDA/audit/revenue?period=90d")
    bad_group_by = client.get("/api/dashboard/v1/tickers/NVDA/audit/cost?group_by=desk")

    assert bad_period.status_code == 422
    assert bad_period.json()["error"]["code"] == "INVALID_PARAMS"
    assert bad_group_by.status_code == 422
    assert bad_group_by.json()["error"]["details"]["group_by"] == "desk"


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


class _RevenueBars:
    name = "fixture"

    def fetch_bars(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        return [
            _minute_bar("2026-06-30T11:00:00-04:00", 100),
            _minute_bar("2026-06-30T11:10:00-04:00", 101),
            _minute_bar("2026-06-30T11:30:00-04:00", 102),
            _minute_bar("2026-06-30T15:50:00-04:00", 110),
        ]


def _client_with_audit_state(
    *,
    model_usage_service: ModelUsageCostService | None = None,
) -> tuple[TestClient, datetime]:
    monitoring_service = MonitoringBusService(InMemoryMonitoringRepository())
    runtime_service = PersistentRuntimeExecutionService(InMemoryPersistentRuntimeRepository())
    timestamp = datetime(2026, 6, 30, 15, 30, tzinfo=UTC)
    _seed_audit_state(runtime_service, timestamp)
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=_DocumentProvider(),
        monitoring_service=monitoring_service,
        runtime_service=runtime_service,
    )
    resolved_model_usage_service = model_usage_service or ModelUsageCostService(
        InMemoryModelUsageRepository(),
        pricing_catalog=_default_model_pricing_catalog(),
    )
    revenue_audit_service = RevenueAuditService(
        InMemoryRevenueAuditRepository(),
        trading_repository=runtime_service.repository,
        market_data_provider=_RevenueBars(),
        config=RevenueAuditConfig(),
    )
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            real_service=RealDashboardOverviewService(
                DashboardStateAPI(scheduler),
                model_usage_service=resolved_model_usage_service,
                revenue_audit_service=revenue_audit_service,
            ),
        )
    )
    return client, timestamp


def _seed_audit_state(
    runtime_service: PersistentRuntimeExecutionService,
    timestamp: datetime,
) -> None:
    runtime_service.repository.save_trading_record(
        TradingRecord(
            record_id="trd_nvda_today",
            source_message_id="std_nvda_audit_001",
            ticker="NVDA",
            source_type=SourceType.MEDIA,
            route="trading_record",
            matched_policy_code="POLICY_DTC_SUPPLY",
            trade_intent=TradeIntent(
                side=TradeSide.LONG,
                conviction=Conviction.HIGH,
                size_bucket=SizeBucket.NORMAL,
                reasoning="Dashboard audit unit-test trade intent.",
            ),
            status=TradeRecordStatus.RECORDED_ONLY,
            audit_snapshot=TradeAuditSnapshot(
                published_at=timestamp - timedelta(minutes=30),
                collected_at=timestamp - timedelta(minutes=25),
                normalized_at=timestamp - timedelta(minutes=22),
                message_bus_event_time=timestamp - timedelta(minutes=20),
                runtime_started_at=timestamp - timedelta(minutes=5),
                intent_generated_at=timestamp,
                decision_source=TradeDecisionSource.W2_POLICY_DIRECT,
                trigger_policy="POLICY_DTC_SUPPLY",
                source_message_id="std_nvda_audit_001",
                runtime_execution_id="pre_nvda_audit_001",
                route="new_dtc",
                trigger_reason="DTC policy matched.",
                message_summary="NVDA audit fixture message.",
                agent_summary="Dashboard audit unit-test trade intent.",
            ),
            created_at=timestamp,
        )
    )
    runtime_service.repository.save_trading_record(
        TradingRecord(
            record_id="trd_nvda_recent",
            source_message_id="std_nvda_audit_002",
            ticker="NVDA",
            source_type=SourceType.MEDIA,
            route="trading_record",
            matched_policy_code="POLICY_DTC_GUIDE",
            trade_intent=TradeIntent(
                side=TradeSide.SHORT,
                conviction=Conviction.MEDIUM,
                size_bucket=SizeBucket.SMALL,
                reasoning="Recent trade intent inside selected period.",
            ),
            status=TradeRecordStatus.RECORDED_ONLY,
            created_at=timestamp - timedelta(days=2),
        )
    )
    runtime_service.repository.save_trading_record(
        TradingRecord(
            record_id="trd_nvda_outside",
            source_message_id="std_nvda_audit_003",
            ticker="NVDA",
            source_type=SourceType.MEDIA,
            route="trading_record",
            trade_intent=TradeIntent(
                side=TradeSide.LONG,
                conviction=Conviction.LOW,
                size_bucket=SizeBucket.SMALL,
                reasoning="Older trade intent outside 7d period.",
            ),
            status=TradeRecordStatus.RECORDED_ONLY,
            created_at=timestamp - timedelta(days=9),
        )
    )
    runtime_service.repository.save_execution(
        RuntimeExecutionRecord(
            execution_id="pre_nvda_audit_001",
            source_message=_source_message("std_nvda_audit_001", timestamp=timestamp),
            route_decision=RouteDecision(
                source_message_id="std_nvda_audit_001",
                ticker="NVDA",
                route=RuntimeRoute.TRADING_RECORD,
                reason="DTC policy matched.",
            ),
            node_traces=[
                RuntimeNodeTrace(
                    node="W1",
                    status="succeeded",
                    duration_ms=1000,
                    started_at=timestamp,
                ),
                RuntimeNodeTrace(
                    node="O3",
                    status="succeeded",
                    duration_ms=2000,
                    started_at=timestamp,
                ),
            ],
            timing={
                "model_audits": [
                    {
                        "provider": "openai",
                        "model": "gpt-4.1-mini",
                        "retry_count": 0,
                        "metadata": {"node": "W1"},
                        "usage": {
                            "input_tokens": 1000,
                            "output_tokens": 200,
                            "total_tokens": 1200,
                        },
                    },
                    {
                        "provider": "openai",
                        "model": "gpt-4.1",
                        "retry_count": 1,
                        "metadata": {"node": "O3", "cost_usd": "0.012"},
                        "usage": {
                            "input_tokens": 800,
                            "output_tokens": 400,
                            "total_tokens": 1200,
                        },
                    },
                ]
            },
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    runtime_service.repository.save_execution(
        RuntimeExecutionRecord(
            execution_id="pre_nvda_audit_old",
            source_message=_source_message(
                "std_nvda_audit_old",
                timestamp=timestamp - timedelta(days=9),
            ),
            route_decision=RouteDecision(
                source_message_id="std_nvda_audit_old",
                ticker="NVDA",
                route=RuntimeRoute.ARCHIVE,
                reason="Old fixture outside selected period.",
            ),
            timing={
                "model_audit": {
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "metadata": {"node": "W2", "cost_usd": "99"},
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }
            },
            created_at=timestamp - timedelta(days=9),
            updated_at=timestamp - timedelta(days=9),
        )
    )


def _source_message(source_message_id: str, *, timestamp: datetime) -> RuntimeSourceMessage:
    return RuntimeSourceMessage(
        source_message_id=source_message_id,
        raw_message_id=f"raw_{source_message_id}",
        ticker="NVDA",
        source_type=SourceType.MEDIA,
        source_id="benzinga_news",
        title="NVDA audit test source message",
        body="NVDA audit test source message.",
        symbols=["NVDA"],
        collected_at=timestamp,
    )


def _minute_bar(timestamp: str, price: float) -> MinuteBar:
    return MinuteBar(
        ticker="NVDA",
        timestamp=datetime.fromisoformat(timestamp),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1_000,
        data_source="fixture:1m",
    )


def _default_model_pricing_catalog() -> ModelPricingCatalog:
    config = json.loads(DEFAULT_PRICING_PATH.read_text(encoding="utf-8"))
    return ModelPricingCatalog(config, discount_rate=0.45, cny_usd_rate=6.8)
