"""Deterministic fixed-ID snapshot lifecycle; no Gateway or order submissions."""

import concurrent.futures
import threading

import pytest

from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import Strategy
from tests.test_trade_execution import profile


def snapshot_session(timeout=0.2, *, completed_end=True):
    events, calls = [], []
    session = IbkrSession(
        profile(strategy=Strategy(request_timeout_seconds=timeout)),
        event_sink=lambda kind, data: events.append((kind, data)),
    )
    session.accounts = [session.profile.expected_account_id]
    session.connection_generation = 1
    timers = []

    class App:
        pending = None

        def reqOpenOrders(self):
            assert self.pending is None
            self.pending = "open"
            calls.append("open")

            def done():
                self.pending = None
                session.open_event.set()

            timer = threading.Timer(0.01, done)
            timers.append(timer)
            timer.start()

        def reqExecutions(self, request, filt):
            assert self.pending is None and session.open_event.is_set()
            calls.append("executions")
            session.requests[request]["event"].set()

        def reqCompletedOrders(self, api_only):
            assert self.pending is None and calls[-1] == "executions"
            calls.append("completed")
            if completed_end:
                session.completed_event.set()  # Valid empty list.

        def reqPositions(self):
            assert session.completed_event.is_set()
            calls.append("positions")
            session.position_event.set()

        def cancelPositions(self):
            pass

        def reqAccountUpdates(self, subscribe, account):
            if subscribe:
                calls.append("account")
                session.account_event.set()

        def disconnect(self):
            calls.append("disconnect")
            for timer in timers:
                timer.cancel()

    def connect():
        if session.app is None:
            calls.append("connect")
            session.app = App()
            session.healthy = True

    session.connect = connect
    return session, calls, events


def test_snapshot_requests_are_ordered_and_empty_completed_list_finishes():
    session, calls, _ = snapshot_session()
    value = session.sync()
    assert value["complete"]
    assert calls == ["connect", "open", "executions", "completed", "positions", "account"]
    assert not session.requests


def test_missing_end_fails_closes_socket_and_retains_failure_evidence():
    session, calls, events = snapshot_session(timeout=0.03, completed_end=False)
    for _ in range(2):
        with pytest.raises(TimeoutError, match="stage=completed_orders"):
            session.sync()
        assert session.app is None and not session.healthy
    assert calls.count("connect") == calls.count("disconnect") == 2
    assert "positions" not in calls and "account" not in calls
    assert [kind for kind, _ in events] == ["sync_failure", "sync_failure"]
    assert events[0][1]["action"] == "DISCONNECT_BEFORE_RETRY"
    assert not session.requests


def test_snapshot_single_flight_does_not_clear_another_call_end_event():
    session, calls, _ = snapshot_session()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: session.sync(), range(2)))
    assert all(v["complete"] for v in values)
    assert calls.count("completed") == calls.count("account") == 2
    assert "disconnect" not in calls
