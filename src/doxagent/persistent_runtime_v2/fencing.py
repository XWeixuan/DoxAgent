"""Fence business writes on the same SQLite transaction as their lease check."""

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from .journal import LeaseLost

_lease: ContextVar[Any] = ContextVar("runtime_write_lease", default=None)


@contextmanager
def write_scope(kind: str, value: Any, clock: Callable[[], datetime]) -> Iterator[None]:
    token = _lease.set((kind, value, clock))
    try:
        yield
    finally:
        _lease.reset(token)


def assert_write_lease(db: sqlite3.Connection) -> None:
    lease = _lease.get()
    if lease is None:
        return
    kind, value, clock = lease
    if kind == "task":
        row = sqlite3.Connection.execute(
            db,
            "SELECT status,owner,token,lease_until FROM runtime_tasks WHERE id=?",
            (value["id"],),
        ).fetchone()
        valid = (
            row
            and row[0] == "RUNNING"
            and row[1] == value["owner"]
            and row[2] == value["token"]
            and row[3] > clock().isoformat()
        )
    else:
        row = sqlite3.Connection.execute(
            db, "SELECT payload_json FROM runtime_v2_effects WHERE effect_id=?", (value.effect_id,)
        ).fetchone()
        current = json.loads(row[0]) if row else {}
        valid = (
            current.get("status") == "RUNNING"
            and current.get("lease_token") == value.lease_token
            and datetime.fromisoformat(current["lease_until"]) > datetime.now(UTC)
        )
    if not valid:
        raise LeaseLost("business write rejected after lease loss")


class FencedConnection(sqlite3.Connection):
    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if _lease.get() and sql.lstrip().split(None, 1)[0].upper() in {
            "INSERT",
            "UPDATE",
            "DELETE",
            "REPLACE",
        }:
            if not self.in_transaction:
                super().execute("BEGIN IMMEDIATE")
            assert_write_lease(self)
        return super().execute(sql, parameters)
