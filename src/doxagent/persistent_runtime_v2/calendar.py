"""Offline exchange calendar with persisted overrides and cached session facts.

API: https://github.com/gerrymanoim/exchange_calendars (session queries).
Calendar loading performs no provider/model or market-data requests.
"""

from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
from typing import Any

from .journal import RuntimeJournal


class MarketCalendar:
    def __init__(self, journal: RuntimeJournal, name: str = "XNYS") -> None:
        self.journal = journal
        self.name = name
        self._calendar: Any = None

    @property
    def calendar_version(self) -> str:
        return f"{self.name}:{version('exchange-calendars')}"

    def is_session(self, day: date) -> bool:
        key = f"{self.name}:{day.isoformat()}"
        override = self.journal.get("calendar_overrides", key)
        if override is not None:
            return bool(override["is_session"])
        cached = self.journal.get("calendar", key)
        try:
            if self._calendar is None:
                import exchange_calendars  # type: ignore[import-untyped]

                self._calendar = exchange_calendars.get_calendar(
                    self.name, start="2000-01-01", end="2040-12-31"
                )
            result = bool(self._calendar.is_session(day.isoformat()))
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
        try:
            bounds = (
                self._calendar.session_open(day.isoformat()).to_pydatetime(),
                self._calendar.session_close(day.isoformat()).to_pydatetime(),
            )
            self.journal.set(
                "calendar_bounds",
                key,
                {"open": bounds[0].isoformat(), "close": bounds[1].isoformat()},
            )
            return bounds
        except Exception:
            cached = self.journal.get("calendar_bounds", key)
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
