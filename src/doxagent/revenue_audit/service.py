"""Batch orchestration and bounded Dashboard queries for revenue audits."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from doxagent.persistent_runtime.repository import (
    InMemoryPersistentRuntimeRepository,
    PersistentRuntimeRepository,
    SQLitePersistentRuntimeRepository,
)
from doxagent.revenue_audit.calculator import anchors_for_record, calculate_record_results
from doxagent.revenue_audit.market_data import (
    MarketDataError,
    MinuteBarProvider,
    provider_from_settings,
)
from doxagent.revenue_audit.repository import (
    InMemoryRevenueAuditRepository,
    RevenueAuditRepository,
    SQLiteRevenueAuditRepository,
)
from doxagent.revenue_audit.schema import (
    RevenueAuditConfig,
    RevenueAuditRecordStatus,
    RevenueAuditResult,
    RevenueAuditRun,
    RevenueAuditRunStatus,
    RevenueBasis,
)
from doxagent.settings import DoxAgentSettings

ET = ZoneInfo("America/New_York")
SUPPORTED_PERIODS = {"today": 1, "7d": 7, "30d": 30}


class RevenueAuditService:
    def __init__(
        self,
        repository: RevenueAuditRepository,
        *,
        trading_repository: PersistentRuntimeRepository,
        market_data_provider: MinuteBarProvider,
        config: RevenueAuditConfig,
        auto_trigger_hour_et: int = 18,
    ) -> None:
        self.repository = repository
        self.trading_repository = trading_repository
        self.market_data_provider = market_data_provider
        self.config = config
        self.auto_trigger_hour_et = auto_trigger_hour_et

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
        *,
        repository: RevenueAuditRepository | None = None,
        trading_repository: PersistentRuntimeRepository | None = None,
    ) -> RevenueAuditService:
        resolved = settings or DoxAgentSettings()
        audit_repository = repository
        if audit_repository is None:
            if resolved.revenue_audit_storage_mode == "memory":
                audit_repository = InMemoryRevenueAuditRepository()
            else:
                audit_repository = SQLiteRevenueAuditRepository(resolved.revenue_audit_sqlite_path)
        resolved_trading_repository = trading_repository
        if resolved_trading_repository is None:
            if resolved.persistent_runtime_storage_mode == "memory":
                resolved_trading_repository = InMemoryPersistentRuntimeRepository()
            else:
                resolved_trading_repository = SQLitePersistentRuntimeRepository(
                    resolved.persistent_runtime_sqlite_path
                )
        return cls(
            audit_repository,
            trading_repository=resolved_trading_repository,
            market_data_provider=provider_from_settings(resolved),
            config=RevenueAuditConfig(
                method_version=resolved.revenue_audit_method_version,
                market_data_provider=resolved.revenue_audit_market_data_provider,
                slippage_bps=resolved.revenue_audit_slippage_bps,
                base_notional_usd=resolved.revenue_audit_base_notional_usd,
                size_multipliers={
                    "small": resolved.revenue_audit_small_multiplier,
                    "normal": resolved.revenue_audit_normal_multiplier,
                    "aggressive": resolved.revenue_audit_aggressive_multiplier,
                },
            ),
            auto_trigger_hour_et=resolved.revenue_audit_auto_trigger_hour_et,
        )

    def audit_date(self, ticker: str, audit_date: date) -> RevenueAuditRun:
        normalized = _ticker(ticker)
        existing = self.repository.get_run(
            ticker=normalized,
            audit_date=audit_date,
            method_version=self.config.method_version,
            config_fingerprint=self.config.fingerprint,
        )
        if existing is not None:
            run = existing.model_copy(
                update={
                    "status": RevenueAuditRunStatus.CALCULATING,
                    "record_count": 0,
                    "result_count": 0,
                    "audited_count": 0,
                    "issue_count": 0,
                    "failure_reason": None,
                    "config_snapshot": self.config.model_dump(mode="json"),
                    "started_at": datetime.now(UTC),
                    "completed_at": None,
                }
            )
        else:
            run = RevenueAuditRun(
                ticker=normalized,
                audit_date=audit_date,
                method_version=self.config.method_version,
                config_fingerprint=self.config.fingerprint,
                status=RevenueAuditRunStatus.CALCULATING,
                config_snapshot=self.config.model_dump(mode="json"),
                started_at=datetime.now(UTC),
            )
        run = self.repository.save_run(run)
        start, end = _day_bounds(audit_date)
        records = self.trading_repository.list_trading_records(
            ticker=normalized,
            created_from=start,
            created_to=end,
            newest_first=False,
            limit=10_000,
        )
        if not records:
            completed = run.model_copy(
                update={
                    "status": RevenueAuditRunStatus.COMPLETED,
                    "completed_at": datetime.now(UTC),
                }
            )
            return self.repository.save_run(completed)

        bars = None
        market_error: MarketDataError | None = None
        anchors = [
            anchor
            for record in records
            if record.trade_intent.side.value in {"long", "short"}
            for anchor in anchors_for_record(record).values()
            if anchor is not None
        ]
        if anchors:
            earliest = min(anchor.astimezone(ET) for anchor in anchors)
            latest = max(anchor.astimezone(ET) for anchor in anchors)
            market_start = datetime.combine(earliest.date(), time(9, 30), tzinfo=ET)
            market_end = datetime.combine(
                latest.date() + timedelta(days=self.config.max_forward_calendar_days),
                time(16, 0),
                tzinfo=ET,
            )
            try:
                bars = self.market_data_provider.fetch_bars(
                    normalized,
                    start=market_start,
                    end=market_end,
                )
            except MarketDataError as exc:
                market_error = exc

        results = [
            result
            for record in records
            for result in calculate_record_results(
                record,
                run=run,
                config=self.config,
                bars=bars,
                market_error=market_error,
                provider_name=self.market_data_provider.name,
            )
        ]
        self.repository.save_results(results)
        audited_count = sum(
            1 for item in results if item.status is RevenueAuditRecordStatus.AUDITED
        )
        issue_results = [
            item
            for item in results
            if item.status
            not in {
                RevenueAuditRecordStatus.AUDITED,
                RevenueAuditRecordStatus.UNSUPPORTED_ACTION,
            }
        ]
        if (
            audited_count == 0
            and issue_results
            and all(item.status is RevenueAuditRecordStatus.FAILED for item in issue_results)
        ):
            status = RevenueAuditRunStatus.FAILED
        elif issue_results:
            status = RevenueAuditRunStatus.PARTIAL
        else:
            status = RevenueAuditRunStatus.COMPLETED
        completed = run.model_copy(
            update={
                "status": status,
                "record_count": len(records),
                "result_count": len(results),
                "audited_count": audited_count,
                "issue_count": len(issue_results),
                "failure_reason": (
                    str(market_error)[:500]
                    if status is RevenueAuditRunStatus.FAILED and market_error is not None
                    else None
                ),
                "completed_at": datetime.now(UTC),
            }
        )
        return self.repository.save_run(completed)

    def audit_due(self, *, now: datetime | None = None) -> list[RevenueAuditRun]:
        current = (now or datetime.now(UTC)).astimezone(ET)
        if current.hour < self.auto_trigger_hour_et:
            return []
        target_date = current.date()
        start, end = _day_bounds(target_date)
        records = self.trading_repository.list_trading_records(
            created_from=start,
            created_to=end,
            newest_first=False,
            limit=10_000,
        )
        runs: list[RevenueAuditRun] = []
        for ticker in sorted({record.ticker for record in records}):
            existing = self.repository.get_run(
                ticker=ticker,
                audit_date=target_date,
                method_version=self.config.method_version,
                config_fingerprint=self.config.fingerprint,
            )
            if existing is not None and existing.status in {
                RevenueAuditRunStatus.CALCULATING,
                RevenueAuditRunStatus.COMPLETED,
                RevenueAuditRunStatus.PARTIAL,
            }:
                continue
            runs.append(self.audit_date(ticker, target_date))
        return runs

    def overview(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        period: str,
        target_date: date,
    ) -> dict[str, object]:
        date_from, date_to = _period_bounds(period, target_date)
        payload = self.repository.overview(
            ticker=ticker,
            basis=basis,
            date_from=date_from,
            date_to=date_to,
            method_version=self.config.method_version,
            config_fingerprint=self.config.fingerprint,
        )
        start, end = _range_datetime_bounds(date_from, date_to)
        records = self.trading_repository.list_trading_records(
            ticker=_ticker(ticker),
            created_from=start,
            created_to=end,
            limit=10_000,
        )
        total_records = len(records)
        auditable_records = sum(
            1 for item in records if item.trade_intent.side.value in {"long", "short"}
        )
        audited_count = int(payload.get("audited_trade_count") or 0)
        payload["trade_intent_count"] = max(
            total_records, int(payload.get("trade_intent_count") or 0)
        )
        payload["auditable_trade_count"] = max(
            auditable_records, int(payload.get("auditable_trade_count") or 0)
        )
        payload["coverage_rate"] = audited_count / auditable_records if auditable_records else None
        if auditable_records and audited_count < auditable_records:
            payload["status"] = (
                RevenueAuditRunStatus.PARTIAL.value
                if audited_count
                else RevenueAuditRunStatus.NOT_STARTED.value
            )
        payload.update(
            {
                "ticker": _ticker(ticker),
                "audit_date": target_date.isoformat(),
                "period": period,
                "basis": basis.value,
                "exit_rule": "15:50 America/New_York, nearest valid regular-session minute",
                "method_version": self.config.method_version,
                "config_fingerprint": self.config.fingerprint,
                "latency_losses": self.repository.latency_losses(
                    ticker=ticker,
                    date_from=date_from,
                    date_to=date_to,
                    method_version=self.config.method_version,
                    config_fingerprint=self.config.fingerprint,
                ),
            }
        )
        return payload

    def trend(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        period: str,
        target_date: date,
    ) -> list[dict[str, object]]:
        date_from, date_to = _period_bounds(period, target_date)
        rows = self.repository.trend(
            ticker=ticker,
            basis=basis,
            date_from=date_from,
            date_to=date_to,
            method_version=self.config.method_version,
            config_fingerprint=self.config.fingerprint,
        )
        by_date = {str(item["date"]): item for item in rows}
        return [
            by_date.get(
                day.isoformat(),
                {
                    "date": day.isoformat(),
                    "pnl_usd": None,
                    "return_pct": None,
                    "trade_intent_count": 0,
                    "auditable_trade_count": 0,
                    "audited_trade_count": 0,
                    "coverage_rate": None,
                    "incomplete": False,
                },
            )
            for day in _dates(date_from, date_to)
        ]

    def list_results(
        self,
        *,
        ticker: str,
        basis: RevenueBasis,
        period: str,
        target_date: date,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[RevenueAuditResult], str | None]:
        date_from, date_to = _period_bounds(period, target_date)
        return self.repository.list_results(
            ticker=ticker,
            basis=basis,
            date_from=date_from,
            date_to=date_to,
            method_version=self.config.method_version,
            config_fingerprint=self.config.fingerprint,
            status=status,
            limit=limit,
            cursor=cursor,
        )

    def result_detail(self, ticker: str, trading_record_id: str) -> list[RevenueAuditResult]:
        return self.repository.result_detail(
            ticker=ticker,
            trading_record_id=trading_record_id,
            method_version=self.config.method_version,
            config_fingerprint=self.config.fingerprint,
        )


def _period_bounds(period: str, target_date: date) -> tuple[date, date]:
    days = SUPPORTED_PERIODS.get(period)
    if days is None:
        raise ValueError(f"Unsupported revenue audit period: {period}")
    return target_date - timedelta(days=days - 1), target_date


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    local_start = datetime.combine(day, time.min, tzinfo=ET)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def _range_datetime_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start, _ = _day_bounds(date_from)
    _, end = _day_bounds(date_to)
    return start, end


def _dates(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=index) for index in range((date_to - date_from).days + 1)]


def _ticker(value: str) -> str:
    return value.strip().upper()
