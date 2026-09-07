"""Local execution ledger. Successful intake and every fill are durable before ACK."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, digest, encode

from .schema import ExecutionProfile
from .strategy import decimal

TERMINAL_ORDERS = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}


class ExecutionRepository:
    def __init__(self, journal: RuntimeJournal) -> None:
        self.journal = journal
        with journal.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS te_meta (key TEXT PRIMARY KEY,value TEXT)")
            version = db.execute("SELECT value FROM te_meta WHERE key='version'").fetchone()
            if version and version[0] != "1":
                raise ValueError("unsupported Trade Executor database version")
            db.execute("INSERT OR IGNORE INTO te_meta VALUES('version','1')")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS te_profiles (
                    revision TEXT PRIMARY KEY, profile_id TEXT, account TEXT, environment TEXT,
                    payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS te_executions (
                    id TEXT PRIMARY KEY, account TEXT, ticker TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS te_jobs (
                    id TEXT PRIMARY KEY, execution_id TEXT, account TEXT, ticker TEXT, leg TEXT,
                    state TEXT, due_at TEXT, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS te_jobs_due ON te_jobs(state,due_at);
                CREATE INDEX IF NOT EXISTS te_jobs_scope ON te_jobs(account,ticker,state);
                CREATE TABLE IF NOT EXISTS te_attempts (
                    id TEXT PRIMARY KEY, job_id TEXT, account TEXT, order_ref TEXT UNIQUE,
                    order_id INTEGER, client_id INTEGER, state TEXT, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS te_attempts_job ON te_attempts(job_id);
                CREATE INDEX IF NOT EXISTS te_attempts_order
                    ON te_attempts(account,client_id,order_id);
                CREATE TABLE IF NOT EXISTS te_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, kind TEXT,
                    payload TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS te_fills (
                    account TEXT, exec_id TEXT, family TEXT, correction INTEGER,
                    attempt_id TEXT, con_id INTEGER, payload TEXT,
                    PRIMARY KEY(account,exec_id));
                CREATE INDEX IF NOT EXISTS te_fills_scope ON te_fills(account,con_id);
                CREATE TABLE IF NOT EXISTS te_lots (
                    id TEXT PRIMARY KEY, account TEXT, con_id INTEGER, payload TEXT);
                CREATE TABLE IF NOT EXISTS te_allocations (
                    account TEXT, con_id INTEGER, fill_id TEXT, lot_id TEXT, qty TEXT, kind TEXT);
                CREATE TABLE IF NOT EXISTS te_lot_adjustments (
                    id TEXT PRIMARY KEY, account TEXT, con_id INTEGER, payload TEXT);
                CREATE TABLE IF NOT EXISTS te_sync_state (
                    account TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS te_acceptance_runs (
                    id TEXT PRIMARY KEY, state TEXT, due_at TEXT, payload TEXT);
            """)

    def import_profile(self, profile: ExecutionProfile) -> str:
        payload = profile.model_dump(mode="json")
        revision = "ep_" + digest(payload)[:24]
        with self.journal.transaction() as db:
            if (
                profile.quote_profile_revision
                and not db.execute(
                    "SELECT 1 FROM te_profiles WHERE revision=?", (profile.quote_profile_revision,)
                ).fetchone()
            ):
                raise ValueError("unknown quote profile revision")
            conflict = db.execute(
                "SELECT 1 FROM te_profiles WHERE account=? AND environment!=?",
                (profile.expected_account_id, profile.environment),
            ).fetchone()
            if conflict:
                raise ValueError("ACCOUNT_ENVIRONMENT_CONFLICT")
            for row in db.execute(
                "SELECT payload FROM te_profiles WHERE account=?", (profile.expected_account_id,)
            ):
                prior = json.loads(row[0])
                if prior["client_id"] != profile.client_id:
                    raise ValueError("ACCOUNT_CLIENT_ID_IS_IMMUTABLE")
            db.execute(
                "INSERT OR IGNORE INTO te_profiles VALUES(?,?,?,?,?)",
                (
                    revision,
                    profile.profile_id,
                    profile.expected_account_id,
                    profile.environment,
                    encode(payload),
                ),
            )
        return revision

    def profile(self, revision: str) -> ExecutionProfile:
        with self.journal.transaction() as db:
            row = db.execute(
                "SELECT payload FROM te_profiles WHERE revision=?", (revision,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown execution profile revision")
        return ExecutionProfile.model_validate_json(row[0])

    def activate(self, revision: str, reason: str) -> None:
        profile = self.profile(revision)
        if not reason.strip():
            raise ValueError("reason required")
        # Same runtime_values table and transaction boundary used by TradeOutputService.record.
        self.journal.set(
            "trade_execution",
            "active",
            {
                "revision": revision,
                "profile": profile.model_dump(mode="json"),
                "reason": reason,
                "activated_at": self.journal.clock().isoformat(),
            },
        )

    def admit(self, intent: dict[str, Any]) -> dict[str, Any]:
        if intent.get("trade", {}).get("decision") not in {"LONG", "SHORT"}:
            raise ValueError("invalid trade direction")
        if datetime.fromisoformat(intent["expires_at"]).tzinfo is None:
            raise ValueError("execution expiry must include timezone")
        pin = intent.get("execution_pin")
        if not pin or intent["status"] not in {"READY", "UNKNOWN", "EXECUTION_ACCEPTED"}:
            raise ValueError("intent not eligible for broker execution")
        profile = self.profile(pin["revision"])
        if profile.model_dump(mode="json") != pin["profile"]:
            raise ValueError("profile pin mismatch")
        identity = intent["intent_id"]
        account = profile.expected_account_id
        value = {
            "id": identity,
            "intent": intent,
            "profile_revision": pin["revision"],
            "account": account,
            "ticker": intent["ticker"],
            "entry_result": None,
            "created_at": self.journal.clock().isoformat(),
        }
        with self.journal.transaction() as db:
            old = db.execute("SELECT payload FROM te_executions WHERE id=?", (identity,)).fetchone()
            if old:
                previous = json.loads(old[0])
                if (
                    previous["account"] != account
                    or previous["profile_revision"] != pin["revision"]
                    or previous["ticker"] != intent["ticker"]
                    or previous["intent"]["trade"]["decision"] != intent["trade"]["decision"]
                ):
                    raise ValueError("immutable execution identity conflict")
            db.execute(
                "INSERT OR IGNORE INTO te_executions VALUES(?,?,?,?)",
                (identity, account, intent["ticker"], encode(value)),
            )
            self._put_job(
                db,
                {
                    "id": identity + ":entry",
                    "execution_id": identity,
                    "account": account,
                    "ticker": intent["ticker"],
                    "leg": "ENTRY",
                    "state": "READY",
                    "due_at": self.journal.clock().isoformat(),
                    "generation": 0,
                    "failures": 0,
                },
            )
        return {"intent_id": identity, "status": "EXECUTION_ACCEPTED"}

    @staticmethod
    def _put_job(db: Any, value: Any) -> Any:
        db.execute(
            "INSERT OR IGNORE INTO te_jobs VALUES(?,?,?,?,?,?,?,?)",
            (
                value["id"],
                value["execution_id"],
                value["account"],
                value["ticker"],
                value["leg"],
                value["state"],
                value["due_at"],
                encode(value),
            ),
        )

    def get(self, table: str, identity: str) -> dict[str, Any] | None:
        if table not in {"executions", "jobs", "attempts", "lots", "acceptance_runs"}:
            raise ValueError("invalid execution table")
        with self.journal.transaction() as db:
            row = db.execute(f"SELECT payload FROM te_{table} WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def require(self, table: str, identity: str) -> dict[str, Any]:
        value = self.get(table, identity)
        if value is None:
            raise ValueError(f"execution record missing: {table}/{identity}")
        return value

    def jobs(self) -> list[dict[str, Any]]:
        with self.journal.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM te_jobs WHERE state IN "
                "('READY','WAIT_SESSION','WAIT_QUOTE','ACTIVE','RECONCILE_REQUIRED') "
                "AND due_at<=? ORDER BY CASE WHEN state IN ('ACTIVE','RECONCILE_REQUIRED') "
                "THEN 0 ELSE 1 END,CASE leg WHEN 'EXIT' THEN 0 ELSE 1 END,due_at,id",
                (self.journal.clock().isoformat(),),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def update_job(self, job: dict[str, Any], *, delay: float = 0, **updates: Any) -> Any:
        job = {
            **job,
            **updates,
            "due_at": (self.journal.clock() + timedelta(seconds=delay)).isoformat(),
        }
        with self.journal.transaction() as db:
            db.execute(
                "UPDATE te_jobs SET state=?,due_at=?,payload=? WHERE id=?",
                (job["state"], job["due_at"], encode(job), job["id"]),
            )
        return job

    def attempts(self, job_id: str) -> list[dict[str, Any]]:
        with self.journal.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM te_attempts WHERE job_id=? ORDER BY id", (job_id,)
            )
            return [json.loads(row[0]) for row in rows]

    def prepare_attempt(
        self, job: Any, data: dict[str, Any], broker_next_id: int, client_id: int
    ) -> dict[str, Any]:
        with self.journal.transaction() as db:
            maximum = (
                db.execute(
                    "SELECT MAX(order_id) FROM te_attempts WHERE account=? AND client_id=?",
                    (job["account"], client_id),
                ).fetchone()[0]
                or 0
            )
            number = max(broker_next_id, maximum + 1)
            serial = db.execute(
                "SELECT COUNT(*) FROM te_attempts WHERE job_id=?", (job["id"],)
            ).fetchone()[0]
            identity = f"{job['id']}:{serial:08d}"
            value = {
                **data,
                "id": identity,
                "job_id": job["id"],
                "account": job["account"],
                "client_id": client_id,
                "order_id": number,
                "order_ref": "DA-" + digest(identity)[:28],
                "state": "PREPARED",
                "prepared_at": self.journal.clock().isoformat(),
            }
            db.execute(
                "INSERT INTO te_attempts VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity,
                    job["id"],
                    job["account"],
                    value["order_ref"],
                    number,
                    client_id,
                    value["state"],
                    encode(value),
                ),
            )
        return value

    def update_attempt(self, attempt: dict[str, Any], **updates: Any) -> dict[str, Any]:
        with self.journal.transaction() as db:
            row = db.execute(
                "SELECT payload FROM te_attempts WHERE id=?", (attempt["id"],)
            ).fetchone()
            value = {**json.loads(row[0]), **updates}
            db.execute(
                "UPDATE te_attempts SET state=?,payload=? WHERE id=?",
                (value["state"], encode(value), value["id"]),
            )
        return value

    def event(self, account: str, kind: str, payload: dict[str, Any]) -> None:
        with self.journal.transaction() as db:
            db.execute(
                "INSERT INTO te_events(account,kind,payload,created_at) VALUES(?,?,?,?)",
                (account, kind, encode(payload), self.journal.clock().isoformat()),
            )

    def apply_order(self, account: str, event: dict[str, Any]) -> None:
        with self.journal.transaction() as db:
            row = db.execute(
                "SELECT payload FROM te_attempts WHERE account=? AND "
                "((client_id=? AND order_id=?) OR order_ref=?)",
                (
                    account,
                    event.get("client_id", -1),
                    event.get("order_id", -1),
                    event.get("order_ref", ""),
                ),
            ).fetchone()
            if not row:
                return
            value = json.loads(row[0])
            status = event.get("status", "")
            old = value.get("broker_status")
            # Late Submitted must not reopen a broker terminal order.
            if old not in TERMINAL_ORDERS or status in TERMINAL_ORDERS:
                value["broker_status"] = status
            if "filled" in event and (old not in TERMINAL_ORDERS or status in TERMINAL_ORDERS):
                value["broker_filled"] = str(decimal(event["filled"]))
            value["perm_id"] = event.get("perm_id") or value.get("perm_id")
            value["broker_seen"] = True
            if event.get("error"):
                value["broker_error"] = event["error"]
            db.execute("UPDATE te_attempts SET payload=? WHERE id=?", (encode(value), value["id"]))

    def apply_fill(self, fill: dict[str, Any], exit_at: str) -> bool:
        qty = decimal(fill["quantity"])
        if qty < 0 or qty != int(qty):
            raise ValueError("NON_INTEGER_FILL")
        with self.journal.transaction() as db:
            row = db.execute(
                "SELECT payload FROM te_attempts WHERE account=? AND "
                "((client_id=? AND order_id=?) OR order_ref=?)",
                (
                    fill["account"],
                    fill.get("client_id", -1),
                    fill.get("order_id", -1),
                    fill.get("order_ref", ""),
                ),
            ).fetchone()
            if not row:
                return False  # External/manual orders never create owned lots.
            attempt = json.loads(row[0])
            if fill["con_id"] != attempt["contract"]["con_id"] or fill["side"] != attempt["side"]:
                raise ValueError("FILL_IDENTITY_MISMATCH")
            exec_id = fill["exec_id"]
            family, sep, revision = exec_id.rpartition(".")
            if not sep or not revision.isdigit():
                family, revision = exec_id, "0"
            value = {**fill, "attempt_id": attempt["id"], "exit_at": exit_at}
            inserted = db.execute(
                "INSERT OR IGNORE INTO te_fills VALUES(?,?,?,?,?,?,?)",
                (
                    fill["account"],
                    exec_id,
                    family,
                    int(revision),
                    attempt["id"],
                    fill["con_id"],
                    encode(value),
                ),
            ).rowcount
            if inserted:
                attempt["broker_seen"] = True
                attempt["perm_id"] = fill.get("perm_id") or attempt.get("perm_id")
                if fill.get("cum_qty") is not None:
                    attempt["broker_filled"] = fill["cum_qty"]
                db.execute(
                    "UPDATE te_attempts SET payload=? WHERE id=?", (encode(attempt), attempt["id"])
                )
                self._rebuild_lots(db, fill["account"], fill["con_id"])
                job = json.loads(
                    db.execute(
                        "SELECT payload FROM te_jobs WHERE id=?", (attempt["job_id"],)
                    ).fetchone()[0]
                )
                if job["leg"] == "ENTRY" and job["state"] == "DONE":
                    entry_ids = {
                        r[0]
                        for r in db.execute(
                            "SELECT id FROM te_attempts WHERE job_id=?", (job["id"],)
                        )
                    }
                    fills = [
                        f
                        for f in self._effective_fills(db, fill["account"], fill["con_id"])
                        if f["attempt_id"] in entry_ids
                    ]
                    execution = json.loads(
                        db.execute(
                            "SELECT payload FROM te_executions WHERE id=?", (job["execution_id"],)
                        ).fetchone()[0]
                    )
                    execution["filled_qty"] = sum(int(decimal(f["quantity"])) for f in fills)
                    execution["filled_notional"] = str(
                        sum(
                            (decimal(f["quantity"]) * decimal(f["price"]) for f in fills),
                            Decimal(0),
                        )
                    )
                    if execution["filled_qty"] and execution.get("entry_result") == "FAILED":
                        execution["entry_result"] = "PARTIAL_FILLED"
                        execution["entry_reason"] = "LATE_FILL_RECONCILED"
                    db.execute(
                        "UPDATE te_executions SET payload=? WHERE id=?",
                        (encode(execution), execution["id"]),
                    )
        return bool(inserted)

    @staticmethod
    def _effective_fills(db: Any, account: Any, con_id: Any = None) -> Any:
        rows = db.execute(
            "SELECT * FROM te_fills WHERE account=?"
            + (" AND con_id=?" if con_id is not None else ""),
            (account, con_id) if con_id is not None else (account,),
        ).fetchall()
        latest: dict[str, Any] = {}
        for row in rows:
            if (
                row["family"] not in latest
                or row["correction"] > latest[row["family"]]["correction"]
            ):
                latest[row["family"]] = row
        return sorted(
            (json.loads(r["payload"]) for r in latest.values()),
            key=lambda f: (f["time"], f["exec_id"]),
        )

    def fills(self, job_id: str) -> list[dict[str, Any]]:
        with self.journal.transaction() as db:
            attempts = db.execute(
                "SELECT id,account FROM te_attempts WHERE job_id=?", (job_id,)
            ).fetchall()
            if not attempts:
                return []
            ids = {r["id"] for r in attempts}
            return [
                f
                for f in self._effective_fills(db, attempts[0]["account"])
                if f["attempt_id"] in ids
            ]

    def _rebuild_lots(self, db: Any, account: str, con_id: int) -> Any:
        lots: dict[str, dict[str, Any]] = {}
        allocations = []
        history = self._effective_fills(db, account, con_id)
        history += [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM te_lot_adjustments WHERE account=? AND con_id=?",
                (account, con_id),
            )
        ]
        for fill in sorted(
            history, key=lambda item: (item["time"], item.get("exec_id", item.get("id")))
        ):
            if fill.get("kind") == "OWNED_ADJUSTMENT":
                lot = lots.get(fill["lot_id"])
                if lot is None or lot["remaining_qty"] + fill["quantity_delta"] < 0:
                    raise ValueError("LOT_ADJUSTMENT_OUT_OF_RANGE")
                lot["remaining_qty"] += fill["quantity_delta"]
                allocations.append(
                    (fill["id"], lot["id"], str(fill["quantity_delta"]), "ADJUSTMENT")
                )
                continue
            attempt = json.loads(
                db.execute(
                    "SELECT payload FROM te_attempts WHERE id=?", (fill["attempt_id"],)
                ).fetchone()[0]
            )
            job = json.loads(
                db.execute(
                    "SELECT payload FROM te_jobs WHERE id=?", (attempt["job_id"],)
                ).fetchone()[0]
            )
            quantity = int(decimal(fill["quantity"]))
            if quantity == 0:
                continue
            sign = 1 if fill["side"] == "BUY" else -1
            if job["leg"] == "EXIT":
                lot = lots.get(job["execution_id"])
                if not lot or quantity > lot["remaining_qty"] or sign == lot["sign"]:
                    raise ValueError("LOT_ALLOCATION_GAP")
                lot["remaining_qty"] -= quantity
                allocations.append((fill["exec_id"], lot["id"], str(quantity), "EXIT"))
                continue
            for lot in sorted(lots.values(), key=lambda v: (v["first_fill_at"], v["id"])):
                if lot["sign"] == sign or quantity == 0:
                    continue
                used = min(quantity, lot["remaining_qty"])
                lot["remaining_qty"] -= used
                quantity -= used
                if used:
                    allocations.append((fill["exec_id"], lot["id"], str(used), "FIFO_OFFSET"))
            if quantity:
                identity = job["execution_id"]
                lot = lots.setdefault(
                    identity,
                    {
                        "id": identity,
                        "account": account,
                        "con_id": con_id,
                        "ticker": job["ticker"],
                        "sign": sign,
                        "remaining_qty": 0,
                        "first_fill_at": fill["time"],
                        "scheduled_exit": fill["exit_at"],
                    },
                )
                if lot["sign"] != sign:
                    raise ValueError("LOT_DIRECTION_CONFLICT")
                lot["remaining_qty"] += quantity
                allocations.append((fill["exec_id"], identity, str(quantity), "ENTRY"))
        # Keep zero projections for corrected-away lots and close their pending schedules.
        for row in db.execute(
            "SELECT payload FROM te_lots WHERE account=? AND con_id=?", (account, con_id)
        ):
            old = json.loads(row[0])
            lots.setdefault(old["id"], {**old, "remaining_qty": 0})
        db.execute("DELETE FROM te_allocations WHERE account=? AND con_id=?", (account, con_id))
        for allocation in allocations:
            db.execute(
                "INSERT INTO te_allocations VALUES(?,?,?,?,?,?)", (account, con_id, *allocation)
            )
        for lot in lots.values():
            override = db.execute(
                "SELECT payload FROM runtime_values "
                "WHERE namespace='execution_exit_overrides' AND key=?",
                (lot["id"],),
            ).fetchone()
            if override:
                lot["scheduled_exit"] = json.loads(override[0])["due_at"]
            db.execute(
                "INSERT OR REPLACE INTO te_lots VALUES(?,?,?,?)",
                (lot["id"], account, con_id, encode(lot)),
            )
            identity = lot["id"] + ":exit"
            row = db.execute("SELECT payload FROM te_jobs WHERE id=?", (identity,)).fetchone()
            if row:
                job = json.loads(row[0])
                # An active exit must reconcile its live order even if a correction reduced the lot.
                if job["state"] not in {"ACTIVE", "RECONCILE_REQUIRED"}:
                    if lot["remaining_qty"] == 0:
                        job["state"] = "DONE"
                    elif job["state"] == "DONE":
                        job["state"] = "READY"
                    db.execute(
                        "UPDATE te_jobs SET state=?,payload=? WHERE id=?",
                        (job["state"], encode(job), identity),
                    )
            elif lot["remaining_qty"]:
                self._put_job(
                    db,
                    {
                        "id": identity,
                        "execution_id": lot["id"],
                        "account": account,
                        "ticker": lot["ticker"],
                        "leg": "EXIT",
                        "state": "READY",
                        "due_at": lot["scheduled_exit"],
                        "generation": 0,
                        "failures": 0,
                    },
                )

    def adjust_lot(
        self,
        identity: str,
        *,
        adjustment_id: str,
        quantity_delta: int,
        effective_at: str,
        reason: str,
    ) -> None:
        lot = self.require("lots", identity)
        if not reason.strip() or datetime.fromisoformat(effective_at).tzinfo is None:
            raise ValueError("dated adjustment and reason required")
        if datetime.fromisoformat(effective_at) > self.journal.clock():
            raise ValueError("future position adjustments cannot be applied")
        value = {
            "id": adjustment_id,
            "lot_id": identity,
            "kind": "OWNED_ADJUSTMENT",
            "quantity_delta": quantity_delta,
            "sign": lot["sign"],
            "time": effective_at,
            "reason": reason,
        }
        with self.journal.transaction() as db:
            busy = db.execute(
                "SELECT 1 FROM te_attempts WHERE account=? AND state NOT IN ('SETTLED','NOT_SENT')",
                (lot["account"],),
            ).fetchone()
            if busy:
                raise ValueError("reconcile active orders before position adjustment")
            old = db.execute(
                "SELECT payload FROM te_lot_adjustments WHERE id=?", (adjustment_id,)
            ).fetchone()
            if old and old[0] != encode(value):
                raise ValueError("immutable adjustment identity conflict")
            db.execute(
                "INSERT OR IGNORE INTO te_lot_adjustments VALUES(?,?,?,?)",
                (adjustment_id, lot["account"], lot["con_id"], encode(value)),
            )
            self._rebuild_lots(db, lot["account"], lot["con_id"])

    def finish(
        self, job: Any, reason: str, *, complete: bool = False, disabled: bool = False
    ) -> Any:
        fills = self.fills(job["id"])
        quantity = sum(int(decimal(f["quantity"])) for f in fills)
        if job["leg"] == "ENTRY":
            result = (
                "DIRECTION_DISABLED"
                if disabled
                else "FILLED"
                if complete and quantity
                else "PARTIAL_FILLED"
                if quantity
                else "FAILED"
            )
            with self.journal.transaction() as db:
                value = json.loads(
                    db.execute(
                        "SELECT payload FROM te_executions WHERE id=?", (job["execution_id"],)
                    ).fetchone()[0]
                )
                value.update(
                    entry_result=result,
                    entry_reason=reason,
                    filled_qty=quantity,
                    filled_notional=str(
                        sum(
                            (decimal(f["quantity"]) * decimal(f["price"]) for f in fills),
                            Decimal(0),
                        )
                    ),
                )
                db.execute(
                    "UPDATE te_executions SET payload=? WHERE id=?", (encode(value), value["id"])
                )
            return self.update_job(job, state="DONE", result=result, reason=reason)
        lot = self.get("lots", job["execution_id"])
        remaining = lot["remaining_qty"] if lot else 0
        result = "EXIT_FILLED" if remaining == 0 else "EXIT_PARTIAL" if quantity else "EXIT_FAILED"
        if remaining:
            self.gap(job, result, reason)
        return self.update_job(
            job, state="DONE" if not remaining else "FAILED", result=result, reason=reason
        )

    def gap(self, job: Any, code: str, detail: str) -> Any:
        self.journal.set(
            "execution_gaps",
            job["id"] + ":" + code,
            {
                "job_id": job["id"],
                "ticker": job["ticker"],
                "code": code,
                "detail": detail,
                "at": self.journal.clock().isoformat(),
            },
        )

    def resume_exit(self, identity: str, reason: str) -> Any:
        job = self.get("jobs", identity + ":exit")
        if not reason.strip() or not job or job["state"] != "FAILED":
            raise ValueError("only failed exit with explicit reason may resume")
        return self.update_job(
            job,
            state="READY",
            generation=job.get("generation", 0) + 1,
            failures=0,
            resume_reason=reason,
        )

    def allocate_order_id(self, account: Any, client_id: Any, suggested: Any) -> Any:
        key = f"{account}:{client_id}"
        with self.journal.transaction() as db:
            row = db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='execution_ids' AND key=?",
                (key,),
            ).fetchone()
            order_id = max(suggested, int(json.loads(row[0])) + 1 if row else suggested)
            maximum = db.execute(
                "SELECT MAX(order_id) FROM te_attempts WHERE account=? AND client_id=?",
                (account, client_id),
            ).fetchone()[0]
            order_id = max(order_id, (maximum or 0) + 1)
            db.execute(
                "INSERT OR REPLACE INTO runtime_values VALUES('execution_ids',?,?)",
                (key, encode(order_id)),
            )
        return order_id
