from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from doxagent.persistent_runtime import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.schema import (
    Conviction,
    SizeBucket,
    TradeAuditSnapshot,
    TradeDecisionSource,
    TradeIntent,
    TradeSide,
    TradingRecord,
)
from doxagent.revenue_audit import (
    InMemoryRevenueAuditRepository,
    MarketDataError,
    MinuteBar,
    RevenueAuditConfig,
    RevenueAuditRecordStatus,
    RevenueAuditRun,
    RevenueAuditRunStatus,
    RevenueAuditService,
    RevenueBasis,
    SQLiteRevenueAuditRepository,
    calculate_record_results,
    select_entry_and_exit,
)

ET = ZoneInfo("America/New_York")
DEFAULT_PUBLISHED_AT = datetime.fromisoformat("2026-07-08T09:29:00-04:00")


class StaticBars:
    name = "test-bars"

    def __init__(self, bars: list[MinuteBar]) -> None:
        self.bars = bars
        self.calls = 0

    def fetch_bars(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        self.calls += 1
        return [bar.model_copy(deep=True) for bar in self.bars]


class FailedBars:
    name = "failed-bars"

    def fetch_bars(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        raise MarketDataError("provider permission denied")


def test_entry_never_uses_bar_before_anchor_and_rolls_late_anchor_forward() -> None:
    bars = [
        _bar("2026-07-08T09:30:00-04:00", 100),
        _bar("2026-07-08T09:31:00-04:00", 101),
        _bar("2026-07-08T15:50:00-04:00", 105),
        _bar("2026-07-09T09:30:00-04:00", 106),
        _bar("2026-07-09T15:50:00-04:00", 108),
    ]

    intraday = select_entry_and_exit(
        bars,
        anchor=datetime.fromisoformat("2026-07-08T09:30:30-04:00"),
    )
    late = select_entry_and_exit(
        bars,
        anchor=datetime.fromisoformat("2026-07-08T15:51:00-04:00"),
    )

    assert intraday is not None
    assert intraday[0].timestamp.isoformat() == "2026-07-08T09:31:00-04:00"
    assert late is not None
    assert late[0].timestamp.isoformat() == "2026-07-09T09:30:00-04:00"
    assert late[1].timestamp.isoformat() == "2026-07-09T15:50:00-04:00"


@pytest.mark.parametrize(
    ("side", "expected_entry", "expected_exit", "expected_theoretical_sign"),
    [
        (TradeSide.LONG, 100.05, 109.945, 1),
        (TradeSide.SHORT, 99.95, 110.055, -1),
    ],
)
def test_long_and_short_slippage_is_always_adverse(
    side: TradeSide,
    expected_entry: float,
    expected_exit: float,
    expected_theoretical_sign: int,
) -> None:
    config = RevenueAuditConfig()
    record = _record(side=side)
    run = _run()
    results = calculate_record_results(
        record,
        run=run,
        config=config,
        bars=[
            _bar("2026-07-08T09:30:00-04:00", 100),
            _bar("2026-07-08T15:50:00-04:00", 110),
        ],
        provider_name="fixture",
    )
    system = next(item for item in results if item.basis is RevenueBasis.SYSTEM_EXECUTABLE)

    assert system.status is RevenueAuditRecordStatus.AUDITED
    assert system.simulated_entry_price == pytest.approx(expected_entry)
    assert system.simulated_exit_price == pytest.approx(expected_exit)
    assert system.theoretical_return_pct is not None
    assert system.theoretical_return_pct * expected_theoretical_sign > 0
    assert system.simulated_return_pct is not None
    assert system.simulated_return_pct < system.theoretical_return_pct


def test_dst_and_weekend_use_actual_new_york_bar_timestamps() -> None:
    winter = select_entry_and_exit(
        [
            _bar("2026-01-05T09:30:00-05:00", 100),
            _bar("2026-01-05T15:50:00-05:00", 101),
        ],
        anchor=datetime.fromisoformat("2026-01-03T12:00:00-05:00"),
    )
    summer = select_entry_and_exit(
        [
            _bar("2026-07-06T09:30:00-04:00", 100),
            _bar("2026-07-06T15:50:00-04:00", 101),
        ],
        anchor=datetime.fromisoformat("2026-07-04T12:00:00-04:00"),
    )

    assert winter is not None and winter[0].timestamp.utcoffset().total_seconds() == -18_000
    assert summer is not None and summer[0].timestamp.utcoffset().total_seconds() == -14_400


def test_missing_published_time_only_blocks_ideal_basis() -> None:
    record = _record(published_at=None)
    results = calculate_record_results(
        record,
        run=_run(),
        config=RevenueAuditConfig(),
        bars=[
            _bar("2026-07-08T09:30:00-04:00", 100),
            _bar("2026-07-08T15:50:00-04:00", 101),
        ],
        provider_name="fixture",
    )
    statuses = {item.basis: item.status for item in results}

    assert statuses[RevenueBasis.IDEAL_SIGNAL] is RevenueAuditRecordStatus.MISSING_TIME
    assert statuses[RevenueBasis.MESSAGE_BUS] is RevenueAuditRecordStatus.AUDITED
    assert statuses[RevenueBasis.SYSTEM_EXECUTABLE] is RevenueAuditRecordStatus.AUDITED


def test_exit_intent_is_explicitly_unsupported() -> None:
    results = calculate_record_results(
        _record(side=TradeSide.EXIT),
        run=_run(),
        config=RevenueAuditConfig(),
        bars=None,
        provider_name="fixture",
    )

    assert {item.status for item in results} == {RevenueAuditRecordStatus.UNSUPPORTED_ACTION}


def test_service_batches_market_request_and_rerun_is_idempotent() -> None:
    trading = InMemoryPersistentRuntimeRepository()
    trading.save_trading_record(_record(record_id="trd_1"))
    trading.save_trading_record(_record(record_id="trd_2"))
    audit_repository = InMemoryRevenueAuditRepository()
    provider = StaticBars(
        [
            _bar("2026-07-08T09:30:00-04:00", 100),
            _bar("2026-07-08T15:50:00-04:00", 102),
        ]
    )
    service = RevenueAuditService(
        audit_repository,
        trading_repository=trading,
        market_data_provider=provider,
        config=RevenueAuditConfig(),
    )

    first = service.audit_date("NVDA", date(2026, 7, 8))
    second = service.audit_date("NVDA", date(2026, 7, 8))
    items, next_cursor = service.list_results(
        ticker="NVDA",
        basis=RevenueBasis.SYSTEM_EXECUTABLE,
        period="today",
        target_date=date(2026, 7, 8),
    )

    assert first.run_id == second.run_id
    assert second.status is RevenueAuditRunStatus.COMPLETED
    assert provider.calls == 2
    assert len(items) == 2
    assert next_cursor is None
    assert len({item.result_id for item in items}) == 2


def test_provider_failure_marks_run_failed_without_touching_trading_records() -> None:
    trading = InMemoryPersistentRuntimeRepository()
    record = _record()
    trading.save_trading_record(record)
    service = RevenueAuditService(
        InMemoryRevenueAuditRepository(),
        trading_repository=trading,
        market_data_provider=FailedBars(),
        config=RevenueAuditConfig(),
    )

    run = service.audit_date("NVDA", date(2026, 7, 8))

    assert run.status is RevenueAuditRunStatus.FAILED
    assert trading.trading_record_for_source(record.source_message_id) is not None


def test_auto_trigger_runs_only_at_or_after_18_et() -> None:
    trading = InMemoryPersistentRuntimeRepository()
    trading.save_trading_record(_record())
    service = RevenueAuditService(
        InMemoryRevenueAuditRepository(),
        trading_repository=trading,
        market_data_provider=StaticBars(
            [
                _bar("2026-07-08T09:30:00-04:00", 100),
                _bar("2026-07-08T15:50:00-04:00", 101),
            ]
        ),
        config=RevenueAuditConfig(),
    )

    before = service.audit_due(now=datetime.fromisoformat("2026-07-08T17:59:00-04:00"))
    after = service.audit_due(now=datetime.fromisoformat("2026-07-08T18:00:00-04:00"))
    repeated = service.audit_due(now=datetime.fromisoformat("2026-07-08T18:01:00-04:00"))

    assert before == []
    assert len(after) == 1
    assert repeated == []


def test_sqlite_repository_uses_weighted_period_return_and_cursor_pagination(
    tmp_path: Path,
) -> None:
    trading = InMemoryPersistentRuntimeRepository()
    trading.save_trading_record(_record(record_id="trd_small", size=SizeBucket.SMALL))
    trading.save_trading_record(_record(record_id="trd_large", size=SizeBucket.AGGRESSIVE))
    repository = SQLiteRevenueAuditRepository(tmp_path / "audit.sqlite3")
    service = RevenueAuditService(
        repository,
        trading_repository=trading,
        market_data_provider=StaticBars(
            [
                _bar("2026-07-08T09:30:00-04:00", 100),
                _bar("2026-07-08T15:50:00-04:00", 110),
            ]
        ),
        config=RevenueAuditConfig(),
    )
    service.audit_date("NVDA", date(2026, 7, 8))

    overview = service.overview(
        ticker="NVDA",
        basis=RevenueBasis.SYSTEM_EXECUTABLE,
        period="today",
        target_date=date(2026, 7, 8),
    )
    first, cursor = service.list_results(
        ticker="NVDA",
        basis=RevenueBasis.SYSTEM_EXECUTABLE,
        period="today",
        target_date=date(2026, 7, 8),
        limit=1,
    )
    second, _ = service.list_results(
        ticker="NVDA",
        basis=RevenueBasis.SYSTEM_EXECUTABLE,
        period="today",
        target_date=date(2026, 7, 8),
        limit=1,
        cursor=cursor,
    )

    assert overview["simulated_return_pct"] == pytest.approx(
        (float(overview["simulated_pnl_usd"]) / 25_000) * 100
    )
    assert cursor is not None
    assert first[0].result_id != second[0].result_id


def test_sqlite_result_scope_query_uses_bounded_composite_index(tmp_path: Path) -> None:
    path = tmp_path / "audit-query-plan.sqlite3"
    SQLiteRevenueAuditRepository(path)

    with sqlite3.connect(path) as connection:
        plan = connection.execute(
            """
            explain query plan
            select result_id, payload_json
            from revenue_audit_results
            where ticker = ?
              and basis = ?
              and audit_date between ? and ?
              and method_version = ?
              and config_fingerprint = ?
            order by intent_generated_at desc, result_id desc
            limit ?
            """,
            (
                "NVDA",
                RevenueBasis.SYSTEM_EXECUTABLE.value,
                "2026-07-01",
                "2026-07-08",
                "paper-trade-v1",
                "config",
                51,
            ),
        ).fetchall()

    detail = " ".join(str(column) for row in plan for column in row)
    assert "revenue_audit_results_scope_idx" in detail
    assert "SCAN revenue_audit_results" not in detail


def _record(
    *,
    record_id: str = "trd_nvda",
    side: TradeSide = TradeSide.LONG,
    size: SizeBucket = SizeBucket.NORMAL,
    published_at: datetime | None = DEFAULT_PUBLISHED_AT,
) -> TradingRecord:
    created_at = datetime.fromisoformat("2026-07-08T13:30:00+00:00")
    return TradingRecord(
        record_id=record_id,
        source_message_id=f"std_{record_id}",
        ticker="NVDA",
        matched_policy_code="POLICY_NVDA",
        route="new_dtc",
        trade_intent=TradeIntent(
            side=side,
            conviction=Conviction.HIGH,
            size_bucket=size,
            reasoning="Fixture intent.",
        ),
        audit_snapshot=TradeAuditSnapshot(
            published_at=published_at,
            collected_at=datetime.fromisoformat("2026-07-08T09:29:20-04:00"),
            normalized_at=datetime.fromisoformat("2026-07-08T09:29:25-04:00"),
            message_bus_event_time=datetime.fromisoformat("2026-07-08T09:29:30-04:00"),
            runtime_started_at=datetime.fromisoformat("2026-07-08T09:29:40-04:00"),
            intent_generated_at=datetime.fromisoformat("2026-07-08T09:30:00-04:00"),
            decision_source=TradeDecisionSource.W2_POLICY_DIRECT,
            trigger_policy="POLICY_NVDA",
            source_message_id=f"std_{record_id}",
            runtime_execution_id=f"pre_{record_id}",
            route="new_dtc",
            trigger_reason="Direct policy match.",
            message_summary="NVDA fixture message.",
            agent_summary="Fixture intent.",
        ),
        created_at=created_at,
    )


def _run() -> RevenueAuditRun:
    config = RevenueAuditConfig()
    return RevenueAuditRun(
        ticker="NVDA",
        audit_date=date(2026, 7, 8),
        method_version=config.method_version,
        config_fingerprint=config.fingerprint,
    )


def _bar(timestamp: str, price: float) -> MinuteBar:
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
