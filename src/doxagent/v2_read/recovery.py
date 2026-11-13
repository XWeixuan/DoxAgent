"""Only explicitly transient storage failures may retry without operator action."""
import errno
import sqlite3


def retryable_storage(exc):
    if isinstance(exc, OSError):
        return exc.errno == errno.ENOSPC
    if isinstance(exc, sqlite3.OperationalError):
        return (getattr(exc, "sqlite_errorcode", 0) & 255) in (
            sqlite3.SQLITE_FULL, sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
        )
    return False


def retry_delay(attempt):
    return (1, 2, 5, 10, 30)[min(attempt, 4)]
