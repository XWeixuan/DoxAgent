"""Intent-level paper-trading revenue audit subsystem."""

from doxagent.revenue_audit.calculator import (
    anchors_for_record,
    calculate_record_results,
    select_entry_and_exit,
)
from doxagent.revenue_audit.market_data import (
    BenzingaMinuteBarProvider,
    MarketDataError,
    MinuteBarProvider,
    MissingMarketDataError,
    TwelveDataMinuteBarProvider,
)
from doxagent.revenue_audit.repository import (
    InMemoryRevenueAuditRepository,
    RevenueAuditRepository,
    SQLiteRevenueAuditRepository,
)
from doxagent.revenue_audit.schema import (
    MinuteBar,
    RevenueAuditConfig,
    RevenueAuditRecordStatus,
    RevenueAuditResult,
    RevenueAuditRun,
    RevenueAuditRunStatus,
    RevenueBasis,
)
from doxagent.revenue_audit.service import RevenueAuditService

__all__ = [
    "BenzingaMinuteBarProvider",
    "InMemoryRevenueAuditRepository",
    "MarketDataError",
    "MinuteBar",
    "MinuteBarProvider",
    "MissingMarketDataError",
    "RevenueAuditConfig",
    "RevenueAuditRecordStatus",
    "RevenueAuditRepository",
    "RevenueAuditResult",
    "RevenueAuditRun",
    "RevenueAuditRunStatus",
    "RevenueAuditService",
    "RevenueBasis",
    "SQLiteRevenueAuditRepository",
    "TwelveDataMinuteBarProvider",
    "anchors_for_record",
    "calculate_record_results",
    "select_entry_and_exit",
]
