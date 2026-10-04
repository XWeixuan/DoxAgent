from datetime import UTC, datetime
from pathlib import Path

from doxagent.runtime_scheduler import (
    DocumentRefreshRequest,
    DocumentSetStatus,
    MarketSessionPhase,
    RefreshRequestSource,
    RuntimeAuditEvent,
    RuntimeHealth,
    SQLiteRuntimeSchedulerRepository,
    TickerRunState,
    TickerRunStatus,
    market_session_phase,
)


def test_market_session_phase_uses_et_mvp_workday_rules() -> None:
    assert market_session_phase(datetime(2026, 6, 29, 11, 15, tzinfo=UTC)) == (
        MarketSessionPhase.PRE_MARKET_DIGEST
    )
    assert market_session_phase(datetime(2026, 6, 30, 11, 15, tzinfo=UTC)) == (
        MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
    )
    assert market_session_phase(datetime(2026, 6, 30, 11, 45, tzinfo=UTC)) == (
        MarketSessionPhase.PRE_MARKET_DIGEST
    )
    assert market_session_phase(datetime(2026, 6, 30, 12, 15, tzinfo=UTC)) == (
        MarketSessionPhase.FORMAL_MONITORING
    )
    assert market_session_phase(datetime(2026, 6, 30, 22, 30, tzinfo=UTC)) == (
        MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
    )
    assert market_session_phase(datetime(2026, 7, 4, 15, 0, tzinfo=UTC)) == (
        MarketSessionPhase.OFF_HOURS_LOW_FREQUENCY
    )


def test_scheduler_sqlite_repository_restores_state_audit_and_refresh_request(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler.sqlite3"
    repository = SQLiteRuntimeSchedulerRepository(path)
    state = repository.upsert_state(
        _state_for_sqlite(
            "NVDA",
            now=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
        )
    )
    request = repository.save_refresh_request(scheduler_request := _refresh_request("NVDA"))
    repository.append_audit_event(scheduler_audit := _audit_event("NVDA", "ticker_started"))

    restored = SQLiteRuntimeSchedulerRepository(path)

    assert restored.get_state("NVDA") == state
    assert restored.list_states()[0].ticker == "NVDA"
    assert restored.list_refresh_requests(ticker="NVDA")[0] == request == scheduler_request
    assert restored.list_audit_events(ticker="NVDA")[0] == scheduler_audit


def _state_for_sqlite(ticker: str, *, now: datetime) -> TickerRunState:
    return TickerRunState(
        ticker=ticker,
        status=TickerRunStatus.RUNNING,
        health=RuntimeHealth.NORMAL,
        session_phase=MarketSessionPhase.FORMAL_MONITORING,
        started_at=now,
        updated_at=now,
        document_run_id="run_nvda_fixture",
        document_status=DocumentSetStatus(ticker=ticker, usable=True, checked_at=now),
        last_monitoring_config_version="doc_monitoring_config_nvda:1:fixture",
    )


def _refresh_request(ticker: str) -> DocumentRefreshRequest:
    return DocumentRefreshRequest(
        ticker=ticker,
        requested_by=RefreshRequestSource.USER,
        reason="fixture refresh request",
    )


def _audit_event(ticker: str, event_type: str) -> RuntimeAuditEvent:
    return RuntimeAuditEvent(
        ticker=ticker,
        event_type=event_type,
        message="fixture audit event",
    )
