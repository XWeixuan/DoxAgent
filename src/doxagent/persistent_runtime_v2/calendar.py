"""Offline exchange calendar with persisted overrides and cached session facts.

API: https://github.com/gerrymanoim/exchange_calendars (session queries).
Calendar loading performs no provider/model or market-data requests.
"""

from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
from typing import Any

from .journal import RuntimeJournal
from doxagent.w3_timing import observed


class MarketCalendar:
    def __init__(self, journal: RuntimeJournal, name: str = "XNYS") -> None:
        self.journal = journal
        self.name = name
        self._calendar: Any = None
        self._calendar_window: tuple[date, date] | None = None
        self._version: str | None = None

    @property
    def calendar_version(self) -> str:
        if self._version is None:
            self._version = f"{self.name}:{version('exchange-calendars')}"
        return self._version

    @observed('calendar.load')
    def _load_calendar(self, day: date | None = None):
        # Searches move at most one year. Construct the queried year and its
        # neighbors, keeping the existing supported horizon and single memo.
        start, end = date(2000, 1, 1), date(2040, 12, 31)
        if day is not None and start <= day <= end:
            start, end = date(max(2000, day.year-1), 1, 1), date(min(2040, day.year+1), 12, 31)
        outside = day is not None and self._calendar_window is not None and not (
            self._calendar_window[0] <= day <= self._calendar_window[1])
        if self._calendar is None or outside:
            import exchange_calendars  # type: ignore[import-untyped]

            self._calendar = exchange_calendars.get_calendar(
                self.name, start=start.isoformat(), end=end.isoformat()
            )
            self._calendar_window = start, end
        return self._calendar

    def is_session(self, day: date) -> bool:
        key = f"{self.name}:{day.isoformat()}"
        override = self.journal.get("calendar_overrides", key)
        if override is not None:
            return bool(override["is_session"])
        cached = self.journal.get("calendar", key)
        try:
            if cached is not None and cached.get("version") == self.calendar_version:
                return bool(cached["session"])
            result = bool(self._load_calendar(day).is_session(day.isoformat()))
            self.journal.set("calendar", key, {"session": result, "version": self.calendar_version})
            return result
        except Exception:
            if cached is None:
                raise
            return bool(cached["session"])

    def next_session(self, day: date) -> date:
        for offset in range(1, 367):
            value = day + timedelta(days=offset)
            if self.is_session(value):
                return value
        raise ValueError("calendar has no session within one year")

    def session_bounds(self, day: date) -> tuple[datetime, datetime] | None:
        if not self.is_session(day):
            return None
        key = f"{self.name}:{day.isoformat()}"
        override = self.journal.get("calendar_overrides", key, {})
        if override.get("open_at") and override.get("close_at"):
            return (
                datetime.fromisoformat(override["open_at"]).astimezone(UTC),
                datetime.fromisoformat(override["close_at"]).astimezone(UTC),
            )
        cached = self.journal.get("calendar_bounds", key)
        try:
            if cached and cached.get("version") == self.calendar_version:
                return datetime.fromisoformat(cached["open"]), datetime.fromisoformat(
                    cached["close"]
                )
            calendar = self._load_calendar(day)
            bounds = (
                calendar.session_open(day.isoformat()).to_pydatetime(),
                calendar.session_close(day.isoformat()).to_pydatetime(),
            )
            self.journal.set(
                "calendar_bounds",
                key,
                {
                    "open": bounds[0].isoformat(),
                    "close": bounds[1].isoformat(),
                    "version": self.calendar_version,
                },
            )
            return bounds
        except Exception:
            if not cached:
                raise
            return datetime.fromisoformat(cached["open"]), datetime.fromisoformat(cached["close"])

    def closed_cycle(self, day: date) -> str | None:
        if self.is_session(day):
            return None
        cursor = day
        for _ in range(366):
            previous = cursor - timedelta(days=1)
            if self.is_session(previous):
                return cursor.isoformat()
            cursor = previous
        raise ValueError("closed cycle exceeds calendar horizon")
