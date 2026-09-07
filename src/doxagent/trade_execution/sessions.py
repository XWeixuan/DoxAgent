"""Calendar and venue hours are separate from the Runtime semantic day."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from doxagent.persistent_runtime_v2.calendar import MarketCalendar

ET = ZoneInfo("America/New_York")


def parse_hours(text: str, zone: str) -> list[tuple[datetime, datetime]]:
    aliases = {"EST": "America/New_York", "US/Eastern": "America/New_York"}
    tz = ZoneInfo(aliases.get(zone, zone))
    spans = []
    for entry in text.split(";"):
        if not entry or "CLOSED" in entry:
            continue
        day, intervals = entry.split(":", 1)
        for interval in intervals.split(","):
            start, end = interval.split("-")
            start = f"{day}:{start}" if ":" not in start else start
            end = f"{day}:{end}" if ":" not in end else end
            a = datetime.strptime(start, "%Y%m%d:%H%M").replace(tzinfo=tz)
            b = datetime.strptime(end, "%Y%m%d:%H%M").replace(tzinfo=tz)
            if b <= a:
                b += timedelta(days=1)
            spans.append((a.astimezone(UTC), b.astimezone(UTC)))
    return spans


class Sessions:
    def __init__(self, journal: Any) -> None:
        self.calendar = MarketCalendar(journal)

    def bounds(self, day: date) -> tuple[datetime, datetime] | None:
        return self.calendar.session_bounds(day)

    def classify(self, now: datetime, contract: dict[str, Any]) -> str:
        local = now.astimezone(ET)
        bounds = self.bounds(local.date())
        spans = parse_hours(
            contract.get("trading_hours", ""), contract.get("time_zone", "US/Eastern")
        )
        if any(a <= now < b for a, b in spans):
            if contract["exchange"] == "OVERNIGHT":
                return "OVERNIGHT"
            return "RTH" if bounds and bounds[0] <= now < bounds[1] else "EXTENDED"
        return "CLOSED"

    def venue(self, now: Any) -> Any:
        local = now.astimezone(ET)
        return "OVERNIGHT" if local.time() < time(4) or local.time() >= time(20) else "SMART"

    def exit_at(self, fill_at: datetime, offset: int = 30) -> datetime:
        day = fill_at.astimezone(ET).date()
        bounds = self.bounds(day)
        if bounds and fill_at < bounds[1] - timedelta(minutes=offset):
            return bounds[1] - timedelta(minutes=offset)
        bounds = self.bounds(self.calendar.next_session(day))
        assert bounds is not None
        return bounds[1] - timedelta(minutes=offset)
