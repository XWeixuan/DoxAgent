"""Four incident tables plus bounded, deduplicated source health samples."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .schema import ActionReceipt, HealthSample, Incident, RepairRound, Stage, utcnow


class LeaseLost(RuntimeError):
    pass


class MaintenanceRepository:
    def __init__(
        self, path: Path, *, clock: Callable[[], datetime] = utcnow, readonly: bool = False
    ):
        self.path, self.clock = path, clock
        self.readonly = readonly
        if readonly:
            if not path.is_file():
                raise FileNotFoundError(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS maintenance_incidents (
                    id TEXT PRIMARY KEY, resource TEXT NOT NULL, stage TEXT NOT NULL,
                    generation INTEGER NOT NULL, owner TEXT, lease_until TEXT, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS incident_resource ON maintenance_incidents(resource);
                CREATE TABLE IF NOT EXISTS maintenance_rounds (
                    id TEXT PRIMARY KEY, incident TEXT NOT NULL, started TEXT NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS maintenance_actions (
                    id TEXT PRIMARY KEY, incident TEXT NOT NULL, resource TEXT NOT NULL,
                    kind TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS maintenance_evidence (
                    id TEXT PRIMARY KEY, incident TEXT NOT NULL, created TEXT NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_health_samples (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, operation TEXT NOT NULL,
                    stage TEXT NOT NULL, completed TEXT NOT NULL, data TEXT NOT NULL,
                    UNIQUE(source,operation,stage));
                CREATE INDEX IF NOT EXISTS sample_time ON source_health_samples(completed);
            """)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.readonly:
            raise RuntimeError("read-only maintenance repository")
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            yield db

    def connection(self) -> sqlite3.Connection:
        if self.readonly:
            return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        return sqlite3.connect(self.path, timeout=5)

    def samples(self, *, since: datetime | None = None) -> list[HealthSample]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT data FROM source_health_samples WHERE completed>=? ORDER BY completed",
                ((since or self.clock() - timedelta(days=7)).isoformat(),),
            )
            return [HealthSample.model_validate_json(r[0]) for r in rows]

    def record_sample(self, sample: HealthSample) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO source_health_samples VALUES(?,?,?,?,?,?)",
                (
                    sample.sample_id,
                    sample.source_id,
                    sample.operation_id,
                    sample.stage,
                    sample.completed_at.isoformat(),
                    sample.model_dump_json(),
                ),
            )
            db.execute(
                "DELETE FROM source_health_samples WHERE source=? AND id NOT IN "
                "(SELECT id FROM source_health_samples WHERE source=? "
                "ORDER BY completed DESC LIMIT 1000)",
                (sample.source_id, sample.source_id),
            )
            db.execute(
                "DELETE FROM source_health_samples WHERE completed<?",
                ((self.clock() - timedelta(days=7)).isoformat(),),
            )

    def incidents(self) -> list[Incident]:
        with self.connection() as db:
            return [
                Incident.model_validate_json(r[0])
                for r in db.execute("SELECT data FROM maintenance_incidents ORDER BY rowid")
            ]

    def get(self, identity: str) -> Incident:
        with self.connection() as db:
            row = db.execute("SELECT data FROM maintenance_incidents WHERE id=?", (identity,))
            found = row.fetchone()
        if not found:
            raise KeyError(identity)
        return Incident.model_validate_json(found[0])

    @staticmethod
    def _write(db: sqlite3.Connection, item: Incident) -> None:
        db.execute(
            "INSERT OR REPLACE INTO maintenance_incidents VALUES(?,?,?,?,?,?,?)",
            (
                item.incident_id,
                item.resource_key,
                item.stage,
                item.generation,
                item.owner,
                item.lease_until.isoformat() if item.lease_until else None,
                item.model_dump_json(),
            ),
        )

    def observe(self, proposed: Incident) -> Incident:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT data FROM maintenance_incidents WHERE resource=? "
                "AND stage NOT IN ('STABLE','CANCELLED') ORDER BY rowid DESC",
                (proposed.resource_key,),
            ).fetchall()
            if rows:
                current = Incident.model_validate_json(rows[0][0])
                current.source_ids = sorted(set(current.source_ids + proposed.source_ids))
                current.affected_bindings = sorted(
                    set(current.affected_bindings + proposed.affected_bindings)
                )
                current.last_observed_at = max(current.last_observed_at, proposed.last_observed_at)
                current.updated_at = self.clock()
                # Observation cannot overwrite an active Controller's receipt or lease.
                self._write(db, current)
                return current
            self._write(db, proposed)
            return proposed

    def claim(self, identity: str, owner: str, *, seconds: int = 90) -> Incident | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT data FROM maintenance_incidents WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                return None
            item = Incident.model_validate_json(row[0])
            if item.lease_until and item.lease_until > self.clock():
                return None
            item.owner = owner
            item.generation += 1
            item.lease_until = self.clock() + timedelta(seconds=seconds)
            self._write(db, item)
            return item

    def save(self, item: Incident, *, release: bool = True) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT generation,owner,lease_until FROM maintenance_incidents WHERE id=?",
                (item.incident_id,),
            ).fetchone()
            if (
                not row
                or row["generation"] != item.generation
                or row["owner"] != item.owner
                or not row["lease_until"]
                or datetime.fromisoformat(row["lease_until"]) <= self.clock()
            ):
                raise LeaseLost(item.incident_id)
            current = db.execute(
                "SELECT data FROM maintenance_incidents WHERE id=?", (item.incident_id,)
            ).fetchone()
            observed = Incident.model_validate_json(current[0])
            item.source_ids = sorted(set(item.source_ids + observed.source_ids))
            item.affected_bindings = sorted(
                set(item.affected_bindings + observed.affected_bindings)
            )
            item.last_observed_at = max(item.last_observed_at, observed.last_observed_at)
            item.updated_at = self.clock()
            if release:
                item.owner, item.lease_until = None, None
            self._write(db, item)

    def operator(self, identity: str, *, retry: bool, reason: str) -> Incident:
        if not reason.strip():
            raise ValueError("operator reason required")
        with self.transaction() as db:
            row = db.execute(
                "SELECT data FROM maintenance_incidents WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise KeyError(identity)
            item = Incident.model_validate_json(row[0])
            if item.lease_until and item.lease_until > self.clock():
                raise RuntimeError("controller action active; retry after lease expires")
            item.generation += 1
            item.owner, item.lease_until = None, None
            item.stage = Stage.TRIAGE if retry else Stage.CANCELLED
            item.context["operator_reason"] = reason
            if retry:
                item.context["retry_round_base"] = item.round_count
                item.context.pop("round_id", None)
            self._write(db, item)
            return item

    def rounds(self, identity: str | None = None) -> list[RepairRound]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT data FROM maintenance_rounds WHERE (? IS NULL OR incident=?)",
                (identity, identity),
            )
            return [RepairRound.model_validate_json(r[0]) for r in rows]

    def save_round(self, item: RepairRound) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO maintenance_rounds VALUES(?,?,?,?)",
                (
                    item.round_id,
                    item.incident_id,
                    item.started_at.isoformat(),
                    item.model_dump_json(),
                ),
            )

    def begin_round(self, item: RepairRound, *, daily_rounds: int, daily_seconds: int) -> None:
        with self.transaction() as db:
            rows = [
                RepairRound.model_validate_json(r[0])
                for r in db.execute("SELECT data FROM maintenance_rounds")
            ]
            if any(r.status in {"PENDING", "RUNNING", "VERIFY"} for r in rows):
                raise RuntimeError("one Coding Worker at a time")
            recent = [r for r in rows if r.started_at >= self.clock() - timedelta(days=1)]
            reserved = sum((r.deadline - r.started_at).total_seconds() for r in recent)
            if (
                len(recent) >= daily_rounds
                or reserved + (item.deadline - item.started_at).total_seconds() > daily_seconds
            ):
                raise ValueError("daily coding/time budget exhausted")
            db.execute(
                "INSERT INTO maintenance_rounds VALUES(?,?,?,?)",
                (
                    item.round_id,
                    item.incident_id,
                    item.started_at.isoformat(),
                    item.model_dump_json(),
                ),
            )

    def actions(self, identity: str | None = None) -> list[ActionReceipt]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT data FROM maintenance_actions WHERE (? IS NULL OR incident=?)",
                (identity, identity),
            )
            return [ActionReceipt.model_validate_json(r[0]) for r in rows]

    def prepare_action(self, action: ActionReceipt) -> ActionReceipt:
        with self.transaction() as db:
            existing = db.execute(
                "SELECT data FROM maintenance_actions WHERE id=?", (action.action_id,)
            ).fetchone()
            if existing:
                return ActionReceipt.model_validate_json(existing[0])
            competing = db.execute(
                "SELECT id FROM maintenance_actions WHERE resource=? AND "
                "status IN ('PREPARED','RUNNING','RECONCILE')",
                (action.resource_key,),
            )
            if competing.fetchone():
                raise RuntimeError("resource action already in progress")
            db.execute(
                "INSERT INTO maintenance_actions VALUES(?,?,?,?,?,?,?)",
                (
                    action.action_id,
                    action.incident_id,
                    action.resource_key,
                    action.kind,
                    action.status,
                    action.created_at.isoformat(),
                    action.model_dump_json(),
                ),
            )
        return action

    def save_action(self, action: ActionReceipt) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE maintenance_actions SET status=?,data=? WHERE id=?",
                (action.status, action.model_dump_json(), action.action_id),
            )

    def save_evidence(self, identity: str, key: str, payload: dict) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO maintenance_evidence VALUES(?,?,?,?)",
                (identity + ":" + key, identity, self.clock().isoformat(), json.dumps(payload)),
            )
