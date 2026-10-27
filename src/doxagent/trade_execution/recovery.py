"""Account-wide infrastructure cooldown, shared by jobs and connection maintenance."""

import sqlite3
from datetime import datetime, timedelta

DELAYS = (2, 5, 10, 30, 60)
ENTRY_FAILURE_LIMIT = 3
ENTRY_ABANDONED = "ENTRY_FAILURE_BUDGET_EXHAUSTED"
ENTRY_MAX_QUEUE_SECONDS = 300
ENTRY_QUEUE_EXPIRED = "ENTRY_QUEUE_AGE_EXCEEDED"


def entry_queue_expired(execution, now):
    # Release time includes intake/delivery lag. Legacy records fall back to admission.
    stamp = execution["intent"].get("released_at") or execution["created_at"]
    return (now - datetime.fromisoformat(stamp)).total_seconds() >= ENTRY_MAX_QUEUE_SECONDS


def storage_busy(exc):
    return isinstance(exc, sqlite3.OperationalError) and (
        (getattr(exc, "sqlite_errorcode", 0) & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
        or str(exc) in {"database is locked", "database table is locked"}
    )


def transient(exc):
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, sqlite3.OperationalError):
        return (getattr(exc, "sqlite_errorcode", 0) & 255) in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        } or str(exc) in {"database is locked", "database table is locked"}
    return str(exc).startswith(
        (
            "IBKR_RECONNECT_BACKOFF",
            "IBKR_HANDSHAKE_TIMEOUT",
            "IBKR_DISCONNECTED",
            "IBKR_REQUEST_TIMEOUT",
            "IBKR_PREVIOUS_SOCKET_STILL_RUNNING",
            "BROKER_ORDER_SYNC_INCOMPLETE",
            "BROKER_POSITION_SYNC_INCOMPLETE",
            "BROKER_ACCOUNT_SYNC_INCOMPLETE",
            "FRESH_QUOTE_UNAVAILABLE",
            "QUOTE_UNAVAILABLE",
        )
    )


class RecoveryDeferred(Exception):
    pass


class AccountRecovery:
    def __init__(self, journal, *, on_failure=None):
        self.journal = journal
        self.states = {}
        self.on_failure = on_failure

    def state(self, account):
        if account not in self.states:
            self.states[account] = self.journal.get("execution_recovery", account, {})
        return self.states[account]

    def delay(self, account):
        value = self.state(account)
        due = value.get("retry_at")
        return (
            max(0, (datetime.fromisoformat(due) - self.journal.clock()).total_seconds())
            if due
            else 0
        )

    def check(self, account):
        if self.delay(account):
            raise RecoveryDeferred(self.state(account).get("error", "ACCOUNT_RECOVERY_WAIT"))

    def fail(self, account, exc):
        if storage_busy(exc):
            return  # Runtime contention is not a failed broker connectivity probe.
        # Several already-running jobs may see the same outage. Count one failed probe.
        if self.delay(account):
            return
        failures = self.state(account).get("failures", 0) + 1
        value = {
            "failures": failures,
            "error": str(exc),
            "retry_at": (
                self.journal.clock() + timedelta(seconds=DELAYS[min(failures - 1, 4)])
            ).isoformat(),
        }
        self.states[account] = value  # Keep cooldown even if the database is temporarily busy.
        self.journal.set("execution_recovery", account, value)
        if self.on_failure:
            self.on_failure(account)

    def success(self, account):
        if self.state(account):
            self.states[account] = {}
            self.journal.set("execution_recovery", account, {})
