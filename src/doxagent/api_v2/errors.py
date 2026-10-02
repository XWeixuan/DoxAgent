from __future__ import annotations

from typing import Any
import sqlite3
import time

from doxagent.v2_read.query_budget import QueryDeadlineExceeded, interrupted


class ApiFailure(Exception):
    def __init__(
        self,
        code: str,
        status: int = 400,
        *,
        retryable: bool = False,
        content_id: str | None = None,
        fields: list[dict[str, str]] | None = None,
    ) -> None:
        self.code, self.status, self.retryable = code, status, retryable
        self.content_id = content_id
        self.fields = fields or []

    @property
    def headers(self):
        return {"Retry-After": "2"} if self.retryable and self.status in {503, 504} else {}

    def payload(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.code,
                "retryable": self.retryable,
                "request_id": request_id,
                "fields": self.fields,
                "content_id": self.content_id,
            }
        }


def classify_failure(exc: Exception, *, query_deadline: float | None = None) -> ApiFailure:
    """Safe categories only; never expose exception text, paths or row values."""
    from doxagent.v2_read.content_files import ContentUnavailable
    if isinstance(exc, ApiFailure):
        return exc
    if isinstance(exc, QueryDeadlineExceeded):
        return ApiFailure("QUERY_TIMEOUT", 504, retryable=True)
    if isinstance(exc, ContentUnavailable):
        return ApiFailure("CONTENT_UNAVAILABLE", 503)
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", 0) & 255
        if code == sqlite3.SQLITE_INTERRUPT and (interrupted() or
                (query_deadline is not None and time.monotonic() >= query_deadline)):
            return ApiFailure("QUERY_TIMEOUT", 504, retryable=True)
        if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return ApiFailure("STORE_BUSY", 503, retryable=True)
        return ApiFailure("STORE_UNAVAILABLE", 503, retryable=code in {
            sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_FULL,
        })
    return ApiFailure("INTERNAL_ERROR", 500)
