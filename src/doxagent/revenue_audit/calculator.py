"""Pure paper-trading calculation rules with no provider or persistence side effects."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from doxagent.persistent_runtime.schema import TradingRecord
from doxagent.revenue_audit.market_data import MarketDataError, MissingMarketDataError
from doxagent.revenue_audit.schema import (
    MinuteBar,
    RevenueAuditConfig,
    RevenueAuditRecordStatus,
    RevenueAuditResult,
    RevenueAuditRun,
    RevenueBasis,
    result_id_for,
)

ET = ZoneInfo("America/New_York")
EXIT_TIME = time(15, 50)


def anchors_for_record(record: TradingRecord) -> dict[RevenueBasis, datetime | None]:
    snapshot = record.audit_snapshot
    return {
        RevenueBasis.IDEAL_SIGNAL: snapshot.published_at if snapshot is not None else None,
        RevenueBasis.MESSAGE_BUS: (
            snapshot.message_bus_event_time if snapshot is not None else None
        ),
        RevenueBasis.SYSTEM_EXECUTABLE: (
            snapshot.intent_generated_at if snapshot is not None else record.created_at
        ),
    }


def calculate_record_results(
    record: TradingRecord,
    *,
    run: RevenueAuditRun,
    config: RevenueAuditConfig,
    bars: list[MinuteBar] | None,
    market_error: MarketDataError | None = None,
    provider_name: str | None = None,
) -> list[RevenueAuditResult]:
    anchors = anchors_for_record(record)
    return [
        _calculate_basis(
            record,
            basis=basis,
            anchor=anchors[basis],
            run=run,
            config=config,
            bars=bars,
            market_error=market_error,
            provider_name=provider_name,
        )
        for basis in RevenueBasis
    ]


def _calculate_basis(
    record: TradingRecord,
    *,
    basis: RevenueBasis,
    anchor: datetime | None,
    run: RevenueAuditRun,
    config: RevenueAuditConfig,
    bars: list[MinuteBar] | None,
    market_error: MarketDataError | None,
    provider_name: str | None,
) -> RevenueAuditResult:
    base = _base_result(
        record,
        basis=basis,
        anchor=anchor,
        run=run,
        config=config,
        provider_name=provider_name,
    )
    side = record.trade_intent.side.value
    if side not in {"long", "short"}:
        return base.model_copy(
            update={
                "status": RevenueAuditRecordStatus.UNSUPPORTED_ACTION,
                "failure_reason": "Independent exit intents have no auditable position context.",
            }
        )
    if anchor is None:
        return base.model_copy(
            update={
                "status": RevenueAuditRecordStatus.MISSING_TIME,
                "failure_reason": (
                    f"{basis.value} time anchor is unavailable and was not substituted."
                ),
            }
        )
    if market_error is not None:
        status = (
            RevenueAuditRecordStatus.MISSING_MARKET_DATA
            if isinstance(market_error, MissingMarketDataError)
            else RevenueAuditRecordStatus.FAILED
        )
        return base.model_copy(update={"status": status, "failure_reason": str(market_error)[:500]})
    if not bars:
        return base.model_copy(
            update={
                "status": RevenueAuditRecordStatus.MISSING_MARKET_DATA,
                "failure_reason": "No regular-session minute bars were available.",
            }
        )
    pair = select_entry_and_exit(bars, anchor=anchor)
    if pair is None:
        return base.model_copy(
            update={
                "status": RevenueAuditRecordStatus.MISSING_MARKET_DATA,
                "failure_reason": (
                    "No valid entry/exit pair was available after the anchor and before "
                    "the 15:50 ET exit boundary within the configured forward window."
                ),
            }
        )
    entry, exit_bar = pair
    slippage = config.slippage_bps / 10_000
    theoretical_entry = entry.open
    theoretical_exit = exit_bar.open
    if side == "long":
        simulated_entry = theoretical_entry * (1 + slippage)
        simulated_exit = theoretical_exit * (1 - slippage)
        theoretical_return = (theoretical_exit - theoretical_entry) / theoretical_entry
        simulated_return = (simulated_exit - simulated_entry) / simulated_entry
    else:
        simulated_entry = theoretical_entry * (1 - slippage)
        simulated_exit = theoretical_exit * (1 + slippage)
        theoretical_return = (theoretical_entry - theoretical_exit) / theoretical_entry
        simulated_return = (simulated_entry - simulated_exit) / simulated_entry
    notional = config.notional_for(record.trade_intent.size_bucket.value)
    return base.model_copy(
        update={
            "status": RevenueAuditRecordStatus.AUDITED,
            "theoretical_entry_time": entry.timestamp,
            "theoretical_entry_price": theoretical_entry,
            "simulated_entry_price": simulated_entry,
            "exit_time": exit_bar.timestamp,
            "theoretical_exit_price": theoretical_exit,
            "simulated_exit_price": simulated_exit,
            "notional_usd": notional,
            "theoretical_return_pct": theoretical_return * 100,
            "simulated_return_pct": simulated_return * 100,
            "theoretical_pnl_usd": notional * theoretical_return,
            "simulated_pnl_usd": notional * simulated_return,
            "data_source": entry.data_source,
            "failure_reason": None,
        }
    )


def select_entry_and_exit(
    bars: list[MinuteBar],
    *,
    anchor: datetime,
) -> tuple[MinuteBar, MinuteBar] | None:
    """Select prices without using a bar timestamp earlier than the time anchor."""

    candidate_time = _ceil_to_minute(anchor.astimezone(ET))
    entries = [
        bar
        for bar in sorted(bars, key=lambda item: item.timestamp)
        if bar.timestamp.astimezone(ET) >= candidate_time
        and bar.timestamp.astimezone(ET).time().replace(tzinfo=None) < EXIT_TIME
    ]
    if not entries:
        return None
    entry = entries[0]
    entry_local = entry.timestamp.astimezone(ET)
    target = datetime.combine(entry_local.date(), EXIT_TIME, tzinfo=ET)
    exit_candidates = [
        bar
        for bar in bars
        if bar.timestamp.astimezone(ET).date() == entry_local.date()
        and bar.timestamp > entry.timestamp
    ]
    if not exit_candidates:
        return None
    exit_bar = min(
        exit_candidates,
        key=lambda item: (
            abs((item.timestamp.astimezone(ET) - target).total_seconds()),
            item.timestamp,
        ),
    )
    return entry, exit_bar


def _base_result(
    record: TradingRecord,
    *,
    basis: RevenueBasis,
    anchor: datetime | None,
    run: RevenueAuditRun,
    config: RevenueAuditConfig,
    provider_name: str | None,
) -> RevenueAuditResult:
    snapshot = record.audit_snapshot
    route = record.route or "unknown"
    decision_source = (
        snapshot.decision_source.value
        if snapshot is not None
        else _historical_decision_source(route)
    )
    intent_generated_at = (
        snapshot.intent_generated_at if snapshot is not None else record.created_at
    )
    size_bucket = record.trade_intent.size_bucket.value
    return RevenueAuditResult(
        result_id=result_id_for(
            record.record_id,
            basis,
            config.method_version,
            config.fingerprint,
        ),
        run_id=run.run_id,
        trading_record_id=record.record_id,
        ticker=record.ticker,
        source_message_id=record.source_message_id,
        audit_date=run.audit_date,
        basis=basis,
        side=record.trade_intent.side.value,
        size_bucket=size_bucket,
        decision_source=decision_source,
        trigger_policy=(
            snapshot.trigger_policy if snapshot is not None else record.matched_policy_code
        ),
        runtime_execution_id=(snapshot.runtime_execution_id if snapshot is not None else None),
        message_summary=snapshot.message_summary if snapshot is not None else None,
        agent_summary=(
            snapshot.agent_summary if snapshot is not None else record.trade_intent.reasoning
        ),
        trigger_reason=snapshot.trigger_reason if snapshot is not None else None,
        published_at=snapshot.published_at if snapshot is not None else None,
        collected_at=snapshot.collected_at if snapshot is not None else None,
        normalized_at=snapshot.normalized_at if snapshot is not None else None,
        message_bus_event_time=(snapshot.message_bus_event_time if snapshot is not None else None),
        runtime_started_at=snapshot.runtime_started_at if snapshot is not None else None,
        intent_generated_at=intent_generated_at,
        anchor_time=anchor,
        slippage_bps=config.slippage_bps,
        size_rule_snapshot={
            "base_notional_usd": config.base_notional_usd,
            "size_bucket": size_bucket,
            "multiplier": config.size_multipliers.get(size_bucket),
        },
        data_source=provider_name,
        method_version=config.method_version,
        config_fingerprint=config.fingerprint,
    )


def _historical_decision_source(route: str) -> str:
    if route == "o3_timeout_trade":
        return "o3_upstream_retained"
    if route == "o3_trade":
        return "o3_duty_expert"
    return "w2_policy_direct"


def _ceil_to_minute(value: datetime) -> datetime:
    floor = value.replace(second=0, microsecond=0)
    if value == floor:
        return floor
    return floor + timedelta(minutes=1)
