from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
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
    TradeIntent,
    TradeRecordStatus,
    TradeSide,
    TradingRecord,
)
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    DocumentBundle,
    DocumentSetStatus,
    InMemoryRuntimeSchedulerRepository,
    UnifiedRuntimeSchedulerService,
)


def test_dashboard_real_revenue_audit_uses_trading_records_and_scheduler_events() -> None:
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
    assert payload["status"] == "not_started"
    assert payload["exit_rule"] == "realized_exit_price_audit_not_integrated"
    assert payload["kpis"] == {
        "today_trade_intent_count": 2,
        "audited_trade_count": 0,
        "today_pnl_usd": None,
        "today_return_pct": None,
        "win_rate": None,
    }
    assert len(payload["trend"]) == 7
    assert payload["trend"][-1]["trade_intent_count"] == 1
    assert [item["record_id"] for item in payload["trade_intents"]] == [
        "trd_nvda_today",
        "trd_nvda_recent",
    ]
    assert payload["trade_intents"][0]["estimated_entry_price"] is None
    assert payload["trade_intents"][0]["status"] == "pending_audit"

    run_response = client.post(
        "/api/dashboard/v1/tickers/NVDA/audit/revenue/run",
        json={"date": query_date, "tz": "UTC"},
    )

    assert run_response.status_code == 200
    run_payload = run_response.json()["data"]
    assert run_payload["ticker"] == "NVDA"
    assert run_payload["date"] == query_date
    assert run_payload["status"] == "not_started"
    assert run_payload["audit_run_id"].startswith("audit_")

    events = client.get(
        "/api/dashboard/v1/events"
        "?ticker=NVDA&event_types=audit.revenue.status_changed&once=true"
    )

    assert events.status_code == 200
    assert "event: audit.revenue.status_changed" in events.text
    assert '"status": "not_started"' in events.text
    assert "realized_pnl_audit_worker" in events.text


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

    events = client.get(
        "/api/dashboard/v1/events"
        "?ticker=NVDA&event_types=audit.cost.status_changed&once=true"
    )

    assert events.status_code == 200
    assert "event: audit.cost.status_changed" in events.text
    assert '"status": "partial"' in events.text


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


def _client_with_audit_state() -> tuple[TestClient, datetime]:
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
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
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
