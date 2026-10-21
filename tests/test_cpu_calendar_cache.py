from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from doxagent.persistent_runtime_v2.calendar import MarketCalendar
from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
from doxagent.persistent_runtime_v2.journal import RuntimeJournal


def calendar(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2 import calendar as module

    version = Mock(return_value="test-version")
    monkeypatch.setattr(module, "version", version)
    journal = RuntimeJournal(tmp_path / "runtime.db")
    owner = MarketCalendar(journal)
    owner._calendar = SimpleNamespace(
        is_session=Mock(return_value=True),
        session_open=Mock(return_value=SimpleNamespace(to_pydatetime=lambda: OPEN)),
        session_close=Mock(return_value=SimpleNamespace(to_pydatetime=lambda: CLOSE)),
    )
    return owner, journal, version


DAY = date(2026, 10, 21)
KEY = "XNYS:" + DAY.isoformat()
OPEN = datetime(2026, 10, 21, 13, 30, tzinfo=UTC)
CLOSE = OPEN + timedelta(hours=6, minutes=30)


def test_warm_session_and_bounds_avoid_calculation_and_writes(tmp_path, monkeypatch):
    owner, journal, version = calendar(tmp_path, monkeypatch)
    assert owner.session_bounds(DAY) == (OPEN, CLOSE)
    write = Mock(wraps=journal.set)
    monkeypatch.setattr(journal, "set", write)
    for _ in range(4):
        assert owner.is_session(DAY)
        assert owner.session_bounds(DAY) == (OPEN, CLOSE)
    write.assert_not_called()
    version.assert_called_once_with("exchange-calendars")
    owner._calendar.is_session.assert_called_once()
    owner._calendar.session_open.assert_called_once()
    assert owner.is_session(DAY + timedelta(days=1))
    assert owner._calendar.is_session.call_count == 2


def test_live_override_has_priority_without_poisoning_cached_calendar(tmp_path, monkeypatch):
    owner, journal, _ = calendar(tmp_path, monkeypatch)
    assert owner.session_bounds(DAY) == (OPEN, CLOSE)
    journal.set("calendar_overrides", KEY, dict(is_session=False))
    assert not owner.is_session(DAY)
    assert owner.session_bounds(DAY) is None
    early = CLOSE - timedelta(hours=3)
    journal.set(
        "calendar_overrides",
        KEY,
        dict(
            is_session=True,
            open_at=OPEN.isoformat(),
            close_at=early.isoformat(),
        ),
    )
    assert owner.session_bounds(DAY) == (OPEN, early)
    journal.delete("calendar_overrides", KEY)
    assert owner.session_bounds(DAY) == (OPEN, CLOSE)


def test_new_library_version_rebuilds_facts_and_legacy_bounds_once(tmp_path, monkeypatch):
    owner, journal, _ = calendar(tmp_path, monkeypatch)
    journal.set("calendar", KEY, dict(session=False, version="XNYS:old"))
    journal.set("calendar_bounds", KEY, dict(open=OPEN.isoformat(), close=CLOSE.isoformat()))
    assert owner.session_bounds(DAY) == (OPEN, CLOSE)
    assert journal.get("calendar", KEY)["version"] == "XNYS:test-version"
    assert journal.get("calendar_bounds", KEY)["version"] == "XNYS:test-version"
    # A restarted instance can use both caches, but loads the library lazily if
    # only the session fact exists for another day.
    fresh = MarketCalendar(journal)
    monkeypatch.setattr(fresh, "_load_calendar", Mock(side_effect=AssertionError("cold load")))
    assert fresh.session_bounds(DAY) == (OPEN, CLOSE)


def test_calendar_failure_preserves_existing_fallback_and_missing_data_error(tmp_path, monkeypatch):
    owner, journal, _ = calendar(tmp_path, monkeypatch)
    owner._calendar.is_session.side_effect = RuntimeError("library unavailable")
    journal.set("calendar", KEY, dict(session=True, version="old"))
    assert owner.is_session(DAY)
    with pytest.raises(RuntimeError, match="unavailable"):
        owner.is_session(DAY + timedelta(days=1))


def test_reconcile_only_writes_when_day_or_mode_changes(tmp_path, monkeypatch):
    now = [datetime(2026, 10, 21, 12, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
    coordinator = RuntimeCoordinator.__new__(RuntimeCoordinator)
    coordinator.journal = journal
    coordinator.calendar = SimpleNamespace(closed_cycle=lambda day: None)
    scheduled = Mock()
    coordinator._schedule = scheduled
    write = Mock(wraps=journal.set)
    monkeypatch.setattr(journal, "set", write)
    coordinator.reconcile("MU")
    coordinator.reconcile("MU")
    assert write.call_count == 1
    now[0] += timedelta(days=1)
    coordinator.reconcile("MU")
    assert write.call_count == 2 and scheduled.call_count == 1
    coordinator.calendar.closed_cycle = lambda day: day.isoformat()
    coordinator.reconcile("MU")
    assert write.call_count == 3
