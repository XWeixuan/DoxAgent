"""Short, serializable control transactions in the Runtime database.

An existing released intent never consults these gates. They govern new work only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, encode

MODES = {"MESSAGE_MONITORING", "PAPER_TRADING", "LIVE_TRADING"}
GLOBAL_BINDING_TICKER = "*"


class ControlError(ValueError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code, self.status = code, status


def control_in(db: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_ticker_control'"
    ).fetchone():
        return None
    row = db.execute("SELECT payload FROM v2_ticker_control WHERE ticker=?", (ticker,)).fetchone()
    return json.loads(row[0]) if row else None


def mode_binding_in(
    db: sqlite3.Connection, ticker: str, mode: str
) -> dict[str, Any] | None:
    """Resolve an exact ticker binding before the mode's global default."""
    ticker = ticker.strip().upper()
    row = db.execute(
        "SELECT payload FROM v2_mode_binding WHERE ticker=? AND mode=?",
        (ticker, mode),
    ).fetchone()
    if row is None and ticker != GLOBAL_BINDING_TICKER:
        row = db.execute(
            "SELECT payload FROM v2_mode_binding WHERE ticker=? AND mode=?",
            (GLOBAL_BINDING_TICKER, mode),
        ).fetchone()
    return json.loads(row[0]) if row else None


def output_permission(
    db: sqlite3.Connection, ticker: str, case_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    state = control_in(db, ticker)
    if state is None:
        return None, None
    admission = db.execute(
        "SELECT payload FROM v2_analysis_admission WHERE identity=?", (case_id,)
    ).fetchone()
    origin = json.loads(admission[0]) if admission else {}
    if not origin.get("trade_eligible") or state["mode"] == "MESSAGE_MONITORING":
        return "ANALYSIS_ONLY", None
    if not state["new_intent_allowed"] or origin.get("removal_epoch", 0) != state["removal_epoch"]:
        return "SUPPRESSED_BY_CONTROL", None
    binding = mode_binding_in(db, ticker, state["mode"])
    if binding is None:
        return "MODE_UNAVAILABLE", None
    return None, binding


class ControlRepository:
    VERSION = 1

    def __init__(self, journal: RuntimeJournal) -> None:
        self.journal = journal

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.journal.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def migrate(self) -> None:
        with self.journal.transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_control_schema'").fetchone():
                if [r[0] for r in db.execute("SELECT version FROM v2_control_schema")] != [
                    self.VERSION
                ]:
                    raise ValueError("unsupported V2 control schema")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS v2_control_schema(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO v2_control_schema VALUES(1);
                CREATE TABLE IF NOT EXISTS v2_business_imports (
                    id TEXT PRIMARY KEY,ticker TEXT,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS v2_mode_binding_history (
                    ticker TEXT, mode TEXT, revision INTEGER, profile_revision TEXT,
                    actor TEXT, effective_at TEXT, PRIMARY KEY(ticker,mode,revision));
                CREATE TABLE IF NOT EXISTS v2_ticker_control (
                    ticker TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS v2_mode_binding (
                    ticker TEXT, mode TEXT, payload TEXT NOT NULL, PRIMARY KEY(ticker,mode));
                CREATE TABLE IF NOT EXISTS v2_operations (
                    id TEXT PRIMARY KEY, ticker TEXT, state TEXT, created_at TEXT, payload TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS v2_operation_active ON v2_operations(ticker)
                    WHERE state IN ('ACCEPTED','RUNNING');
                CREATE INDEX IF NOT EXISTS v2_operation_pending ON v2_operations(state,created_at);
                CREATE TABLE IF NOT EXISTS v2_idempotency (
                    scope TEXT, key_hash TEXT, body_hash TEXT, operation_id TEXT,
                    PRIMARY KEY(scope,key_hash));
                CREATE TABLE IF NOT EXISTS v2_control_ack (
                    ticker TEXT, epoch INTEGER, consumer TEXT, payload TEXT,
                    PRIMARY KEY(ticker,epoch,consumer));
                CREATE TABLE IF NOT EXISTS v2_analysis_admission (
                    identity TEXT PRIMARY KEY, ticker TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS v2_dispatch_ticket (
                    identity TEXT PRIMARY KEY, ticker TEXT, epoch INTEGER, created_at TEXT);
                CREATE TABLE IF NOT EXISTS v2_control_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, kind TEXT, payload TEXT,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS v2_control_events_ticker
                    ON v2_control_events(ticker,seq);
            """)

    def get(self, ticker: str) -> dict[str, Any] | None:
        with self.read() as db:
            return control_in(db, ticker.upper())

    def _save(self, db: sqlite3.Connection, state: dict[str, Any], kind: str) -> None:
        db.execute(
            "INSERT INTO v2_ticker_control VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE "
            "SET revision=excluded.revision,payload=excluded.payload",
            (state["ticker"], state["revision"], encode(state)),
        )
        db.execute(
            "INSERT INTO v2_control_events(ticker,kind,payload,created_at) VALUES(?,?,?,?)",
            (state["ticker"], kind, encode(state), self.journal.clock().isoformat()),
        )

    def bind(
        self,
        ticker: str,
        mode: str,
        revision: str,
        *,
        expected: str | None = None,
        actor: str = "local-admin",
    ) -> None:
        from doxagent.trade_execution.repository import ExecutionRepository

        ticker = ticker.strip().upper()
        if ticker != GLOBAL_BINDING_TICKER and not re.fullmatch(
            r"[A-Z][A-Z0-9.\-]{0,14}", ticker
        ):
            raise ControlError("VALIDATION_FAILED", 422)
        profile = ExecutionRepository(self.journal).profile(revision)
        if mode not in {"PAPER_TRADING", "LIVE_TRADING"}:
            raise ControlError("VALIDATION_FAILED", 422)
        if profile.environment != ("PAPER" if mode == "PAPER_TRADING" else "LIVE"):
            raise ControlError("MODE_UNAVAILABLE")
        with self.journal.transaction() as db:
            old = db.execute(
                "SELECT payload FROM v2_mode_binding WHERE ticker=? AND mode=?",
                (ticker, mode),
            ).fetchone()
            if old and json.loads(old[0])["revision"] == revision:
                return
            if old and json.loads(old[0])["revision"] != expected:
                raise ControlError("REVISION_CONFLICT", 412)
            number = db.execute(
                "SELECT coalesce(max(revision),0)+1 FROM v2_mode_binding_history "
                "WHERE ticker=? AND mode=?",
                (ticker, mode),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO v2_mode_binding_history VALUES(?,?,?,?,?,?)",
                (ticker, mode, number, revision, actor, self.journal.clock().isoformat()),
            )
            db.execute(
                "INSERT INTO v2_mode_binding VALUES(?,?,?) ON CONFLICT(ticker,mode) "
                "DO UPDATE SET payload=excluded.payload",
                (
                    ticker,
                    mode,
                    encode({"revision": revision, "profile": profile.model_dump(mode="json")}),
                ),
            )

    def submit(
        self,
        ticker: str,
        kind: str,
        *,
        actor: str,
        key: str,
        body: dict[str, Any],
        expected: str | None = None,
    ) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", ticker):
            raise ControlError("VALIDATION_FAILED", 422)
        if not 8 <= len(key) <= 128 or not key.isascii():
            raise ControlError("IDEMPOTENCY_KEY_REQUIRED", 428)
        if kind not in {"START", "PAUSE", "RESTART", "REMOVE", "RESUME_INITIALIZATION"}:
            raise ControlError("VALIDATION_FAILED", 422)
        scope = f"{actor}:{kind}:{ticker}"
        if kind == "RESUME_INITIALIZATION":
            scope += ":" + str(body.get("initialization_id", ""))
        kh, bh = (
            hashlib.sha256(key.encode()).hexdigest(),
            hashlib.sha256(encode(body).encode()).hexdigest(),
        )
        now = self.journal.clock().isoformat()
        with self.journal.transaction() as db:
            prior = db.execute(
                "SELECT body_hash,operation_id FROM v2_idempotency WHERE scope=? AND key_hash=?",
                (scope, kh),
            ).fetchone()
            if prior:
                if prior[0] != bh:
                    raise ControlError("IDEMPOTENCY_CONFLICT")
                return self._operation(db, prior[1])
            state = control_in(db, ticker)
            if state and expected is None:
                raise ControlError("PRECONDITION_REQUIRED", 428)
            if state and expected != str(state["revision"]):
                raise ControlError("REVISION_CONFLICT", 412)
            if not state and kind != "START":
                raise ControlError("TICKER_NOT_FOUND", 404)
            if kind == "RESUME_INITIALIZATION" and (
                not body.get("initialization_id")
                or body["initialization_id"] != state["initialization_id"]
            ):
                raise ControlError("RESOURCE_NOT_FOUND", 404)
            if db.execute(
                "SELECT 1 FROM v2_operations WHERE ticker=? AND state IN ('ACCEPTED','RUNNING')",
                (ticker,),
            ).fetchone():
                raise ControlError("OPERATION_IN_PROGRESS")
            mode = body.get("monitor_mode", (state or {}).get("mode", "MESSAGE_MONITORING"))
            if mode not in MODES:
                raise ControlError("VALIDATION_FAILED", 422)
            if kind in {"START", "RESTART"} and mode != "MESSAGE_MONITORING":
                if mode_binding_in(db, ticker, mode) is None:
                    raise ControlError("MODE_UNAVAILABLE")
            if state and state["removed"] and kind != "START":
                raise ControlError("TICKER_REMOVED")
            if state and kind in {"PAUSE", "RESTART"} and state.get("initialization_incomplete"):
                raise ControlError("INITIALIZATION_IN_PROGRESS")
            if kind == "RESUME_INITIALIZATION" and not state.get("initialization_failed"):
                raise ControlError("NO_FAILED_INITIALIZATION")
            if not state:
                state = {
                    "ticker": ticker,
                    "revision": 0,
                    "epoch": 0,
                    "removal_epoch": 0,
                    "mode": mode,
                    "requested_mode": mode,
                    "status": "INITIALIZING",
                    "removed": False,
                    "removed_at": None,
                    "analysis_allowed": False,
                    "new_intent_allowed": False,
                    "initialization_id": None,
                    "activation_id": None,
                    "cutoff_at": None,
                }
            state["revision"] += 1
            state["requested_mode"] = mode
            if kind in {"PAUSE", "REMOVE"}:
                state.update(
                    epoch=state["epoch"] + 1,
                    analysis_allowed=False,
                    new_intent_allowed=False,
                    cutoff_at=now,
                )
                state["admission_allowed"] = False
                state["initialization_allowed"] = False
            else:
                state["epoch"] += 1
                state["admission_allowed"] = True
                state["initialization_allowed"] = True
            if kind == "REMOVE":
                state["removal_epoch"] += 1
                state["minimum_epoch"] = state["epoch"] + 1
            op = {
                "id": uuid4().hex,
                "ticker": ticker,
                "kind": kind,
                "actor": actor,
                "state": "ACCEPTED",
                "created_at": now,
                "completed_at": None,
                "body": body,
                "epoch": state["epoch"],
                "steps": {},
                "error": None,
                "outcome": None,
                "attempts": 0,
                "next_attempt_at": now,
            }
            db.execute(
                "INSERT INTO v2_operations VALUES(?,?,?,?,?)",
                (op["id"], ticker, op["state"], now, encode(op)),
            )
            db.execute("INSERT INTO v2_idempotency VALUES(?,?,?,?)", (scope, kh, bh, op["id"]))
            self._save(db, state, kind)
            return op

    @staticmethod
    def _operation(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
        row = db.execute("SELECT payload FROM v2_operations WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ControlError("RESOURCE_NOT_FOUND", 404)
        return json.loads(row[0])

    def operation(self, identity: str) -> dict[str, Any]:
        with self.read() as db:
            return self._operation(db, identity)

    def update_operation(self, op: dict[str, Any]) -> None:
        with self.journal.transaction() as db:
            db.execute(
                "UPDATE v2_operations SET state=?,payload=? WHERE id=?",
                (op["state"], encode(op), op["id"]),
            )

    def pending(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.read() as db:
            rows = db.execute(
                "SELECT payload FROM v2_operations WHERE state IN "
                "('ACCEPTED','RUNNING') ORDER BY created_at,id LIMIT ?",
                (limit,),
            )
            return [json.loads(r[0]) for r in rows]

    def settle(
        self,
        identity: str,
        *,
        activation_id: str | None = None,
        initialization_id: str | None = None,
    ) -> dict[str, Any]:
        with self.journal.transaction() as db:
            op = self._operation(db, identity)
            if op["state"] == "SUCCEEDED":
                return op
            state = control_in(db, op["ticker"])
            assert state is not None
            if op["state"] != "RUNNING" and op["state"] != "ACCEPTED":
                raise ControlError("OPERATION_TERMINAL")
            if state["epoch"] != op["epoch"]:
                raise ControlError("OPERATION_SUPERSEDED")
            now = self.journal.clock().isoformat()
            if op["kind"] in {"START", "RESTART"} and not initialization_id:
                previous_mode = state["mode"]
                state.update(
                    mode=state["requested_mode"],
                    analysis_allowed=True,
                    status="RUNNING",
                    removed=False,
                    removed_at=None,
                    activation_id=activation_id or state["activation_id"],
                )
                state["new_intent_allowed"] = state["mode"] != "MESSAGE_MONITORING"
                if previous_mode == "MESSAGE_MONITORING" or not state.get("mode_effective_at"):
                    state["mode_effective_at"] = now
                op["outcome"] = "RUNNING"
            elif initialization_id:
                state["initialization_id"] = initialization_id
                state["initialization_incomplete"] = True
                state["initialization_failed"] = False
                state["removed"] = False
                state["removed_at"] = None
                op["outcome"] = (
                    "INITIALIZATION_RESUMED"
                    if op["kind"] == "RESUME_INITIALIZATION"
                    else "INITIALIZATION_QUEUED"
                )
            else:
                state["status"] = "STOPPED" if op["kind"] == "REMOVE" else "PAUSED"
                state["removed"] = op["kind"] == "REMOVE"
                state["removed_at"] = now if state["removed"] else None
                op["outcome"] = "REMOVED" if state["removed"] else "PAUSED"
            state["revision"] += 1
            op.update(state="SUCCEEDED", completed_at=now)
            op["ticker_state"] = dict(state)
            self._save(db, state, "settled")
            db.execute(
                "UPDATE v2_operations SET state=?,payload=? WHERE id=?",
                (op["state"], encode(op), identity),
            )
            return op

    def fail(self, identity: str, code: str) -> None:
        with self.journal.transaction() as db:
            op = self._operation(db, identity)
            if op["state"] not in {"ACCEPTED", "RUNNING"}:
                return
            state = control_in(db, op["ticker"])
            if state and state["epoch"] == op["epoch"]:
                state["requested_mode"] = state["mode"]
                state["control_error"] = code
                state["revision"] += 1
                self._save(db, state, "operation.failed")
                op["ticker_state"] = dict(state)
            op.update(state="FAILED", error=code, completed_at=self.journal.clock().isoformat())
            db.execute(
                "UPDATE v2_operations SET state=?,payload=? WHERE id=?",
                (op["state"], encode(op), identity),
            )

    def admit(
        self,
        identity: str,
        ticker: str,
        published_at: datetime | None = None,
        *,
        origin_identity: str | None = None,
    ) -> None:
        with self.journal.transaction() as db:
            state = control_in(db, ticker)
            if state is None:
                return
            if not state["analysis_allowed"]:
                raise ControlError("ANALYSIS_PAUSED")
            eligible = state["mode"] != "MESSAGE_MONITORING"
            if published_at and state.get("mode_effective_at"):
                eligible &= published_at >= datetime.fromisoformat(state["mode_effective_at"])
            value = {
                "origin_epoch": state["epoch"],
                "origin_mode": state["mode"],
                "trade_eligible": eligible,
                "removal_epoch": state["removal_epoch"],
                "admitted_at": self.journal.clock().isoformat(),
            }
            if origin_identity:
                origin = db.execute(
                    "SELECT payload FROM v2_analysis_admission WHERE identity=? AND ticker=?",
                    (origin_identity, ticker),
                ).fetchone()
                if origin:
                    value = json.loads(origin[0])
                    if value["removal_epoch"] != state["removal_epoch"]:
                        raise ControlError("ANALYSIS_REMOVED")
            db.execute(
                "INSERT OR IGNORE INTO v2_analysis_admission VALUES(?,?,?)",
                (identity, ticker, encode(value)),
            )

    def activate_candidate(self, ticker: str, epoch: int, activation_id: str) -> bool:
        with self.journal.transaction() as db:
            state = control_in(db, ticker)
            if not state or state["epoch"] != epoch or not state.get("admission_allowed"):
                return False
            if state.get("activation_id") == activation_id and state["analysis_allowed"]:
                return True
            now = self.journal.clock().isoformat()
            if state["mode"] == "MESSAGE_MONITORING" or not state.get("mode_effective_at"):
                state["mode_effective_at"] = now
            state.update(
                mode=state["requested_mode"],
                status="RUNNING",
                analysis_allowed=True,
                activation_id=activation_id,
                initialization_incomplete=False,
                initialization_failed=False,
                revision=state["revision"] + 1,
            )
            state["new_intent_allowed"] = state["mode"] != "MESSAGE_MONITORING"
            self._save(db, state, "candidate.admitted")
            return True

    def dispatch(self, identity: str, ticker: str, *, case_id: str | None = None) -> None:
        with self.journal.transaction() as db:
            state = control_in(db, ticker)
            if state is None:
                return
            if not state["analysis_allowed"]:
                raise ControlError("ANALYSIS_PAUSED")
            if case_id:
                origin = db.execute(
                    "SELECT payload FROM v2_analysis_admission WHERE identity=?", (case_id,)
                ).fetchone()
                if not origin or json.loads(origin[0])["removal_epoch"] != state["removal_epoch"]:
                    raise ControlError("ANALYSIS_REMOVED")
            db.execute(
                "INSERT OR IGNORE INTO v2_dispatch_ticket VALUES(?,?,?,?)",
                (identity, ticker, state["epoch"], self.journal.clock().isoformat()),
            )

    def defer(self, task: dict[str, Any]) -> None:
        """Return a leased task without consuming its technical failure budget."""
        with self.journal.transaction() as db:
            db.execute(
                "UPDATE runtime_tasks SET status='PENDING',owner=NULL,lease_until=NULL,"
                "due_at=?,updated_at=? WHERE id=? AND token=? AND owner=?",
                (
                    (self.journal.clock() + timedelta(seconds=5)).isoformat(),
                    self.journal.clock().isoformat(),
                    task["id"],
                    task["token"],
                    task["owner"],
                ),
            )
