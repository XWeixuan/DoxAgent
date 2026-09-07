"""Shared ET semantic clock. UTC storage; no market-session trading gate."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def semantic_day(at: datetime) -> date:
    if at.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    local = at.astimezone(EASTERN)
    return local.date() if local.hour >= 2 else local.date() - timedelta(days=1)


def boundary(day: date, *, hour: int = 2, minute: int = 0) -> datetime:
    local = datetime.combine(day, time(hour, minute), EASTERN)
    # Round-trip detects nonexistent spring-forward wall times.
    while local.astimezone(UTC).astimezone(EASTERN).replace(tzinfo=None) != local.replace(
        tzinfo=None
    ):
        local += timedelta(minutes=1)
    return local.astimezone(UTC)


def expires_at(day: date) -> datetime:
    return boundary(day + timedelta(days=1))
