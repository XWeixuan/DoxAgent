"""Pure page calendar using the same exchange sessions and ET02 boundaries as Runtime."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from doxagent.api_v2.dto import available, coverage, missing
from doxagent.persistent_runtime_v2.calendar import MarketCalendar
from doxagent.semantic_clock import boundary, semantic_day

from .repository import instant


class CalendarCache:
    def __init__(self, values: dict[tuple[str, str], Any] | None = None) -> None:
        self.values = values or {}

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        return self.values.get((namespace, key), default)

    def set(self, namespace: str, key: str, value: Any) -> None:
        self.values[(namespace, key)] = value


class PageCalendar:
    def __init__(self, cache: CalendarCache | None = None) -> None:
        self.calendar = MarketCalendar(cache or CalendarCache())  # type: ignore[arg-type]

    def previous(self, before: date, count: int = 1) -> list[date]:
        days = []
        for offset in range(1, count * 4 + 367):
            day = before - timedelta(days=offset)
            if self.calendar.is_session(day):
                days.append(day)
            if len(days) == count:
                return list(reversed(days))
        raise ValueError("CALENDAR_UNAVAILABLE")

    def clock(self, now: datetime, *, maintenance_pending: bool | None = None) -> dict[str, Any]:
        day = semantic_day(now)
        bounds = self.calendar.session_bounds(day)
        if bounds:
            open_at, close_at = bounds
            if now < boundary(day, hour=4):
                session, start, end = "OVERNIGHT", boundary(day), boundary(day, hour=4)
            elif now < open_at:
                session, start, end = "PRE_MARKET", boundary(day, hour=4), open_at
            elif now < close_at:
                session, start, end = "REGULAR", open_at, close_at
            elif now.astimezone(ZoneInfo("America/New_York")).hour < 20:
                session, start, end = "POST_MARKET", close_at, boundary(day, hour=20)
            else:
                session, start, end = (
                    "OVERNIGHT",
                    boundary(day, hour=20),
                    boundary(day + timedelta(days=1)),
                )
        else:
            session = (
                None
                if maintenance_pending is None
                else ("CLOSED_MAINTENANCE" if maintenance_pending else "CLOSED_SLEEP")
            )
            start = end = None
        return {
            "semantic_day": str(day),
            "timezone": "America/New_York",
            "boundary_local_time": "02:00:00",
            "is_trading_day": available(bool(bounds)),
            "previous_trading_day": available(str(self.previous(day)[0])),
            "calendar_version": self.calendar.calendar_version,
            "session": available(session) if session else missing(),
            "session_start_at": available(instant(start)) if start else missing(),
            "session_end_at": available(instant(end)) if end else missing(),
            "next_minute_at": instant(now.replace(second=0, microsecond=0) + timedelta(minutes=1)),
        }

    def window(self, days: list[date], now: datetime) -> dict[str, Any]:
        start, end = boundary(days[0]), boundary(days[-1] + timedelta(days=1))
        return {
            "start_at": instant(start),
            "end_at": instant(end),
            "observed_until": instant(min(now, end)),
            "trading_days": [str(d) for d in days],
            "trading_day_count": len(days),
            "membership": "LISTED_TRADING_DAYS",
            "coverage": coverage(
                complete=now >= end,
                at=instant(min(now, end)),
                reasons=[] if now >= end else ["WINDOW_INCOMPLETE"],
            ),
        }

    def period(
        self, selected: str, now: datetime, *, first_at: datetime | None = None
    ) -> dict[str, Any]:
        day = semantic_day(now)
        if selected == "ALL":
            first = first_at or now
            start_day = semantic_day(first)
            count = sum(
                self.calendar.is_session(start_day + timedelta(days=i))
                for i in range((day - start_day).days + 1)
            )
            return {
                "selected": selected,
                "current": {
                    "start_at": instant(first),
                    "end_at": instant(now),
                    "observed_until": instant(now),
                    "trading_days": [],
                    "trading_day_count": count,
                    "membership": "ALL_SEMANTIC_DAYS",
                    "coverage": coverage(at=instant(now)),
                },
                "previous": None,
                "comparison_applicable": False,
                "comparison_reasons": ["NO_PREVIOUS_WINDOW"],
            }
        if selected == "PREVIOUS_TRADING_DAY":
            current = self.previous(day)
        elif selected == "CURRENT_TRADING_DAY":
            if not self.calendar.is_session(day):
                raise ValueError("NON_TRADING_DAY")
            current = [day]
        elif selected in {"TRADING_DAYS_7", "TRADING_DAYS_30"}:
            current = self.previous(day + timedelta(days=1), int(selected.rsplit("_", 1)[1]))
        else:
            raise ValueError("INVALID_PERIOD")
        previous = self.previous(current[0], len(current))
        complete = now >= boundary(current[-1] + timedelta(days=1))
        return {
            "selected": selected,
            "current": self.window(current, now),
            "previous": self.window(previous, now),
            "comparison_applicable": complete,
            "comparison_reasons": [] if complete else ["WINDOW_INCOMPLETE"],
        }
