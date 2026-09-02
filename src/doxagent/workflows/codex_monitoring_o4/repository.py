"""Durable O4 queue, plan, settlement, thread and coordination state."""

from __future__ import annotations

import sqlite3
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

from .schema import (
    DeliveryCheckpoint,
    DeliverySettlement,
    MonitoringConfigurationPlan,
    O4Request,
    O4RequestStatus,
    O4ThreadSlot,
    RepairClaim,
    RepairSettlement,
    utc_now,
)


class MonitoringO4Repository:
    """SQLite repository whose rows are all restart-safe JSON contracts."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    def _initialize(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS o4_requests (
                    request_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    node TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_o4_requests_status
                    ON o4_requests(status, ticker);
                CREATE TABLE IF NOT EXISTS o4_plans (
                    plan_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(plan_id, plan_version)
                );
                CREATE INDEX IF NOT EXISTS idx_o4_plans_ticker
                    ON o4_plans(ticker, plan_version);
                CREATE TABLE IF NOT EXISTS o4_delivery_checkpoints (
                    plan_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(plan_id, plan_version)
                );
                CREATE TABLE IF NOT EXISTS o4_delivery_settlements (
                    request_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS o4_repair_settlements (
                    request_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS o4_thread_slots (
                    ticker TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS o4_ticker_leases (
                    ticker TEXT PRIMARY KEY,
                    owner_request_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS o4_repair_claims (
                    repair_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def recover_interrupted_requests(self) -> int:
        """Requeue RUNNING rows when the singleton O4 worker starts."""

        with self._lock, self._connection:
            self._connection.execute("DELETE FROM o4_ticker_leases")
            rows = self._connection.execute(
                "SELECT request_id, payload_json FROM o4_requests WHERE status = ?",
                (O4RequestStatus.RUNNING.value,),
            ).fetchall()
            for row in rows:
                request = O4Request.model_validate_json(row["payload_json"])
                request.status = O4RequestStatus.PENDING
                request.error = "recovered after O4 worker restart"
                request.updated_at = utc_now()
                self._connection.execute(
                    "UPDATE o4_requests SET status=?, payload_json=? WHERE request_id=?",
                    (request.status.value, request.model_dump_json(), request.request_id),
                )
        return len(rows)

    def enqueue(self, request: O4Request) -> O4Request:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT payload_json FROM o4_requests WHERE dedupe_key = ?",
                (request.dedupe_key,),
            ).fetchone()
            if row is not None:
                return O4Request.model_validate_json(row["payload_json"])
            self._connection.execute(
                """INSERT INTO o4_requests(
                       request_id, ticker, node, dedupe_key, status, payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    request.request_id,
                    request.ticker,
                    request.node.value,
                    request.dedupe_key,
                    request.status.value,
                    request.model_dump_json(),
                ),
            )
        return request

    def save_request(self, request: O4Request) -> None:
        request.updated_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE o4_requests
                   SET status = ?, payload_json = ? WHERE request_id = ?""",
                (request.status.value, request.model_dump_json(), request.request_id),
            )

    def get_request(self, request_id: str) -> O4Request | None:
        row = self._connection.execute(
            "SELECT payload_json FROM o4_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return O4Request.model_validate_json(row["payload_json"]) if row else None

    def next_pending(self) -> O4Request | None:
        row = self._connection.execute(
            """SELECT payload_json FROM o4_requests
               WHERE status = ? ORDER BY rowid LIMIT 1""",
            (O4RequestStatus.PENDING.value,),
        ).fetchone()
        return O4Request.model_validate_json(row["payload_json"]) if row else None

    def list_requests(
        self,
        *,
        ticker: str | None = None,
        status: O4RequestStatus | None = None,
    ) -> list[O4Request]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if ticker:
            clauses.append("ticker = ?")
            parameters.append(ticker.upper())
        if status:
            clauses.append("status = ?")
            parameters.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT payload_json FROM o4_requests{where} ORDER BY rowid", parameters
        ).fetchall()
        return [O4Request.model_validate_json(row["payload_json"]) for row in rows]

    def save_plan(self, plan: MonitoringConfigurationPlan) -> None:
        """Insert an immutable plan; a conflicting rewrite is rejected."""

        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT payload_json FROM o4_plans
                   WHERE plan_id = ? AND plan_version = ?""",
                (plan.plan_id, plan.plan_version),
            ).fetchone()
            payload = plan.model_dump_json()
            if row is not None:
                if row["payload_json"] != payload:
                    raise ValueError("configuration plan is immutable")
                return
            self._connection.execute(
                """INSERT INTO o4_plans(plan_id, plan_version, ticker, payload_json)
                   VALUES (?, ?, ?, ?)""",
                (plan.plan_id, plan.plan_version, plan.ticker, payload),
            )

    def get_plan(self, plan_id: str, plan_version: int) -> MonitoringConfigurationPlan | None:
        row = self._connection.execute(
            """SELECT payload_json FROM o4_plans
               WHERE plan_id = ? AND plan_version = ?""",
            (plan_id, plan_version),
        ).fetchone()
        return MonitoringConfigurationPlan.model_validate_json(row["payload_json"]) if row else None

    def latest_plan(self, ticker: str) -> MonitoringConfigurationPlan | None:
        row = self._connection.execute(
            """SELECT payload_json FROM o4_plans WHERE ticker = ?
               ORDER BY rowid DESC LIMIT 1""",
            (ticker.upper(),),
        ).fetchone()
        return MonitoringConfigurationPlan.model_validate_json(row["payload_json"]) if row else None

    def save_delivery_checkpoint(self, value: DeliveryCheckpoint) -> None:
        value.updated_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO o4_delivery_checkpoints(plan_id, plan_version, payload_json)
                   VALUES (?, ?, ?)
                   ON CONFLICT(plan_id, plan_version) DO UPDATE SET
                     payload_json=excluded.payload_json""",
                (value.plan_id, value.plan_version, value.model_dump_json()),
            )

    def get_delivery_checkpoint(
        self, plan_id: str, plan_version: int
    ) -> DeliveryCheckpoint | None:
        row = self._connection.execute(
            """SELECT payload_json FROM o4_delivery_checkpoints
               WHERE plan_id = ? AND plan_version = ?""",
            (plan_id, plan_version),
        ).fetchone()
        return DeliveryCheckpoint.model_validate_json(row["payload_json"]) if row else None

    def save_delivery_settlement(self, value: DeliverySettlement) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO o4_delivery_settlements(request_id, payload_json) VALUES (?, ?)
                   ON CONFLICT(request_id) DO UPDATE SET payload_json=excluded.payload_json""",
                (value.request_id, value.model_dump_json()),
            )

    def save_repair_settlement(self, value: RepairSettlement) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO o4_repair_settlements(request_id, payload_json) VALUES (?, ?)
                   ON CONFLICT(request_id) DO UPDATE SET payload_json=excluded.payload_json""",
                (value.request_id, value.model_dump_json()),
            )

    def save_thread(self, slot: O4ThreadSlot) -> None:
        slot.updated_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO o4_thread_slots(ticker, payload_json) VALUES (?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET payload_json=excluded.payload_json""",
                (slot.ticker.upper(), slot.model_dump_json()),
            )

    def get_thread(self, ticker: str) -> O4ThreadSlot | None:
        row = self._connection.execute(
            "SELECT payload_json FROM o4_thread_slots WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
        return O4ThreadSlot.model_validate_json(row["payload_json"]) if row else None

    def acquire_ticker_lease(
        self, ticker: str, owner_request_id: str, *, lease_seconds: int = 7_500
    ) -> bool:
        ticker = ticker.upper()
        now = utc_now()
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT owner_request_id, expires_at FROM o4_ticker_leases WHERE ticker = ?",
                (ticker,),
            ).fetchone()
            if row is not None and row["owner_request_id"] != owner_request_id:
                if datetime_from_sql(row["expires_at"]) > now:
                    return False
            self._connection.execute(
                """INSERT INTO o4_ticker_leases(ticker, owner_request_id, expires_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                     owner_request_id=excluded.owner_request_id,
                     expires_at=excluded.expires_at""",
                (ticker, owner_request_id, expires_at.isoformat()),
            )
        return True

    def release_ticker_lease(self, ticker: str, owner_request_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM o4_ticker_leases WHERE ticker = ? AND owner_request_id = ?",
                (ticker.upper(), owner_request_id),
            )

    def claim_repair(
        self,
        repair_key: str,
        owner_request_id: str,
        *,
        lease_seconds: int = 7_500,
    ) -> RepairClaim:
        now = utc_now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT payload_json FROM o4_repair_claims WHERE repair_key = ?",
                (repair_key,),
            ).fetchone()
            if row is not None:
                claim = RepairClaim.model_validate_json(row["payload_json"])
                if claim.completed_at is None and claim.expires_at > now:
                    if owner_request_id != claim.owner_request_id:
                        claim.linked_request_ids = list(
                            dict.fromkeys([*claim.linked_request_ids, owner_request_id])
                        )
                    self._save_claim(claim)
                    return claim
            claim = RepairClaim(
                repair_key=repair_key,
                owner_request_id=owner_request_id,
                expires_at=now + timedelta(seconds=lease_seconds),
            )
            self._save_claim(claim)
            return claim

    def complete_repair_claim(self, repair_key: str, owner_request_id: str) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT payload_json FROM o4_repair_claims WHERE repair_key = ?",
                (repair_key,),
            ).fetchone()
            if row is None:
                return
            claim = RepairClaim.model_validate_json(row["payload_json"])
            if claim.owner_request_id != owner_request_id:
                return
            claim.completed_at = utc_now()
            self._save_claim(claim)

    def _save_claim(self, claim: RepairClaim) -> None:
        self._connection.execute(
            """INSERT INTO o4_repair_claims(repair_key, payload_json) VALUES (?, ?)
               ON CONFLICT(repair_key) DO UPDATE SET payload_json=excluded.payload_json""",
            (claim.repair_key, claim.model_dump_json()),
        )

    def close(self) -> None:
        self._connection.close()


def datetime_from_sql(value: str):
    from datetime import datetime

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("stored lease timestamp must be timezone-aware")
    return parsed


__all__ = ["MonitoringO4Repository"]
