"""Consumer-local control mirrors, checked inside each consumer's write transaction."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .repository import ControlError


def migrate(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS v2_consumer_control "
        "(ticker TEXT PRIMARY KEY,epoch INTEGER NOT NULL,payload TEXT NOT NULL)"
    )


def state_in(db: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_consumer_control'").fetchone():
        return None
    row = db.execute("SELECT payload FROM v2_consumer_control WHERE ticker=?", (ticker,)).fetchone()
    return json.loads(row[0]) if row else None


def states_in(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_consumer_control'").fetchone():
        return {}
    return {
        r[0]: json.loads(r[1]) for r in db.execute("SELECT ticker,payload FROM v2_consumer_control")
    }


def apply(db: sqlite3.Connection, state: dict[str, Any]) -> None:
    migrate(db)
    db.execute(
        "INSERT INTO v2_consumer_control VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE "
        "SET epoch=excluded.epoch,payload=excluded.payload "
        "WHERE v2_consumer_control.payload IS NOT excluded.payload AND ("
        "excluded.epoch>v2_consumer_control.epoch OR "
        "(excluded.epoch=v2_consumer_control.epoch AND "
        "json_extract(excluded.payload,'$.revision') >= "
        "json_extract(v2_consumer_control.payload,'$.revision')))",
        (state["ticker"], state["epoch"], json.dumps(state, sort_keys=True)),
    )


def permit(
    db: sqlite3.Connection, ticker: str, *, epoch: int | None = None, initialization: bool = False
) -> None:
    state = state_in(db, ticker)
    if state is None:
        return
    allowed = state.get("initialization_allowed") if initialization else state["analysis_allowed"]
    if not allowed:
        raise ControlError("ANALYSIS_PAUSED")
    if (epoch or 0) < state.get("minimum_epoch", 0):
        raise ControlError("ANALYSIS_REMOVED")
