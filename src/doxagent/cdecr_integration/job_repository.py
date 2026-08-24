"""Durable ticker-job state, single-writer leases, and Runtime activity projections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from doxagent.cdecr_integration.contracts import (
    RuntimeActivitySnapshot,
    TickerJobState,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TickerJobRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ticker_jobs (
                    job_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(market, ticker, mode, as_of)
                );
                CREATE INDEX IF NOT EXISTS idx_ticker_jobs_scope_stage
                    ON ticker_jobs(market, ticker, stage, updated_at DESC);

                CREATE TABLE IF NOT EXISTS ticker_locks (
                    market TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY(market, ticker)
                );

                CREATE TABLE IF NOT EXISTS atomic_runtime_activity (
                    job_id TEXT NOT NULL REFERENCES ticker_jobs(job_id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL,
                    last_observed_at TEXT,
                    eligible_until TEXT,
                    is_active INTEGER NOT NULL,
                    PRIMARY KEY(job_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS package_runtime_activity (
                    job_id TEXT NOT NULL REFERENCES ticker_jobs(job_id) ON DELETE CASCADE,
                    package_id TEXT NOT NULL,
                    active_atomic_count INTEGER NOT NULL,
                    is_active INTEGER NOT NULL,
                    PRIMARY KEY(job_id, package_id)
                );
                """
            )

    def save(self, state: TickerJobState) -> None:
        payload = state.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ticker_jobs(
                    job_id, market, ticker, mode, as_of, stage, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    stage=excluded.stage,
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                (
                    state.job_id,
                    state.market,
                    state.ticker,
                    state.mode.value,
                    state.as_of.isoformat(),
                    state.stage.value,
                    payload,
                    state.updated_at.isoformat(),
                ),
            )

    def get(self, job_id: str) -> TickerJobState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM ticker_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return TickerJobState.model_validate_json(str(row[0]))

    def latest(self, *, market: str, ticker: str) -> TickerJobState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json FROM ticker_jobs
                WHERE market=? AND ticker=?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (market.upper(), ticker.upper()),
            ).fetchone()
        return None if row is None else TickerJobState.model_validate_json(str(row[0]))

    def save_activity(self, job_id: str, snapshot: RuntimeActivitySnapshot) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM atomic_runtime_activity WHERE job_id=?", (job_id,)
            )
            connection.execute(
                "DELETE FROM package_runtime_activity WHERE job_id=?", (job_id,)
            )
            connection.executemany(
                """
                INSERT INTO atomic_runtime_activity(
                    job_id,event_id,last_observed_at,eligible_until,is_active
                ) VALUES (?,?,?,?,?)
                """,
                [
                    (
                        job_id,
                        item.runtime_atomic_id,
                        item.last_observed_at.isoformat() if item.last_observed_at else None,
                        item.eligible_until.isoformat() if item.eligible_until else None,
                        int(item.is_active),
                    )
                    for item in snapshot.atomics
                ],
            )
            connection.executemany(
                """
                INSERT INTO package_runtime_activity(
                    job_id,package_id,active_atomic_count,is_active
                ) VALUES (?,?,?,?)
                """,
                [
                    (
                        job_id,
                        item.runtime_package_id,
                        item.active_atomic_count,
                        int(item.is_active),
                    )
                    for item in snapshot.packages
                ],
            )
            connection.commit()

    @contextmanager
    def ticker_lock(
        self,
        *,
        market: str,
        ticker: str,
        owner: str,
        lease_seconds: int = 7200,
    ) -> Iterator[None]:
        now = _utc_now()
        expires_at = now + timedelta(seconds=lease_seconds)
        normalized_market = market.upper()
        normalized_ticker = ticker.upper()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner,expires_at FROM ticker_locks WHERE market=? AND ticker=?",
                (normalized_market, normalized_ticker),
            ).fetchone()
            if row is not None:
                existing_expiry = datetime.fromisoformat(str(row[1]))
                if existing_expiry > now and str(row[0]) != owner:
                    connection.rollback()
                    raise RuntimeError(
                        f"ticker {normalized_market}:{normalized_ticker} is locked by another job"
                    )
            connection.execute(
                """
                INSERT INTO ticker_locks(market,ticker,owner,expires_at)
                VALUES (?,?,?,?)
                ON CONFLICT(market,ticker) DO UPDATE SET
                    owner=excluded.owner, expires_at=excluded.expires_at
                """,
                (normalized_market, normalized_ticker, owner, expires_at.isoformat()),
            )
            connection.commit()
        try:
            yield
        finally:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM ticker_locks WHERE market=? AND ticker=? AND owner=?",
                    (normalized_market, normalized_ticker, owner),
                )


def dump_job_status(state: TickerJobState) -> str:
    """Stable compact JSON for the status CLI."""

    return json.dumps(state.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
