"""Local execution ledger. Successful intake and every fill are durable before ACK."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, digest, encode

from . import groups
from .capital import CapitalLedger
from .schema import ExecutionProfile, historical_profile, legacy_entry_profile
from .strategy import decimal

TERMINAL_ORDERS = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}


class ExecutionRepository:
    def __init__(self, journal: RuntimeJournal, *, initialize=True) -> None:
        self.journal = journal
        if not initialize:
            return
        with journal.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS te_meta (key TEXT PRIMARY KEY,value TEXT)")
            version = db.execute("SELECT value FROM te_meta WHERE key='version'").fetchone()
            if version and version[0] not in {"1", "2"}:
                raise ValueError("unsupported Trade Executor database version")
            db.execute("INSERT OR IGNORE INTO te_meta VALUES('version','2')")
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
                CREATE TABLE IF NOT EXISTS te_account_cycles (
                    account TEXT NOT NULL, environment TEXT NOT NULL, cycle_id TEXT NOT NULL,
                    status TEXT NOT NULL, equity TEXT NOT NULL, captured_at TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(account,environment,cycle_id));
                CREATE UNIQUE INDEX IF NOT EXISTS te_account_cycles_active
                    ON te_account_cycles(account,environment) WHERE status='ACTIVE';
            """)
            if version and version[0] == "1":
                db.execute("UPDATE te_meta SET value='2' WHERE key='version'")
            from doxagent.exit_strategy.repository import initialize

            initialize(db)
            from .capital import initialize as initialize_capital

            initialize_capital(db)

    def transaction(self, *, write=True, timeout=0.2):
        # A contended Runtime writer must defer, not block the shared loop for 30s.
        return self.journal.transaction(write=write, timeout=timeout)

    def import_profile(self, profile: ExecutionProfile) -> str:
        payload = profile.model_dump(mode="json")
        revision = "ep_" + digest(payload)[:24]
        with self.transaction() as db:
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
            from .capital import put

            put(db, "execution_account_profiles", profile.environment, revision)
        return revision

    def profile(self, revision: str) -> ExecutionProfile:
        with self.transaction(write=False) as db:
            row = db.execute(
                "SELECT payload FROM te_profiles WHERE revision=?", (revision,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown execution profile revision")
        return historical_profile(json.loads(row[0]))

    def profile_payload(self, revision: str) -> dict[str, Any]:
        with self.transaction(write=False) as db:
            row = db.execute(
                "SELECT payload FROM te_profiles WHERE revision=?", (revision,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown execution profile revision")
        return json.loads(row[0])

    def activate(self, revision: str, reason: str) -> None:
        self.profile(revision)
        if not reason.strip():
            raise ValueError("reason required")
        # Same runtime_values table and transaction boundary used by TradeOutputService.record.
        self.journal.set(
            "trade_execution",
            "active",
            {
                "revision": revision,
                "profile": self.profile_payload(revision),
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
        if self.profile_payload(pin["revision"]) != pin["profile"]:
            raise ValueError("profile pin mismatch")
        if legacy_entry_profile(pin["profile"]):
            raise ValueError("LEGACY_ENTRY_REMOVED")
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
        try:
            groups.initialize_execution(value, profile)
        except ValueError as exc:
            if str(exc) not in {"CASH_SHORT_SUBSTITUTE_DISABLED", "CASH_SHORT_SUBSTITUTE_MISSING"}:
                raise
            value.update(
                ownership_version=2,
                group_state="CLOSED",
                legs=[],
                entry_result="DIRECTION_DISABLED",
                entry_reason=str(exc),
            )
        with self.transaction() as db:
            old = db.execute("SELECT payload FROM te_executions WHERE id=?", (identity,)).fetchone()
            from .configuration import banned_in

            banned = banned_in(db, intent["ticker"], self.journal.clock())
            if not old and banned:
                return dict(intent_id=identity, status="BANNED_TRADE_DAY", **banned)

            from doxagent.exit_strategy.repository import policy_pin

            value["exit_policy_revision"] = None
            if value.get("trading_config", {}).get("trailing_profit_enabled"):
                from doxagent.exit_strategy.schema import ExitPolicy

                policy = ExitPolicy().model_dump(mode="json")
                revision = "xp_" + digest(policy)[:24]
                db.execute(
                    "INSERT OR IGNORE INTO ts_policies VALUES(?,?)", (revision, encode(policy))
                )
                value["exit_policy_revision"] = (
                    policy_pin(db, account, profile.environment, intent["ticker"]) or revision
                )
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
            if not old:
                for leg in value.get("legs", []):
                    self._put_job(
                        db,
                        {
                            "id": leg["id"] + ":entry",
                            "execution_id": identity,
                            "leg_id": leg["id"],
                            "role": leg["role"],
                            "traded_ticker": leg["traded_ticker"],
                            "side": leg["side"],
                            "account": account,
                            "ticker": intent["ticker"],
                            "leg": "ENTRY",
                            "state": "READY" if leg["role"] == "MAIN" else "WAIT_MAIN",
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
        with self.transaction(write=False) as db:
            row = db.execute(f"SELECT payload FROM te_{table} WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def require(self, table: str, identity: str) -> dict[str, Any]:
        value = self.get(table, identity)
        if value is None:
            raise ValueError(f"execution record missing: {table}/{identity}")
        return value

    def jobs(self) -> list[dict[str, Any]]:
        with self.transaction(write=False) as db:
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
        with self.transaction() as db:
            db.execute(
                "UPDATE te_jobs SET state=?,due_at=?,payload=? WHERE id=?",
                (job["state"], job["due_at"], encode(job), job["id"]),
            )
        return job

    def attempts(self, job_id: str) -> list[dict[str, Any]]:
        with self.transaction(write=False) as db:
            rows = db.execute(
                "SELECT payload FROM te_attempts WHERE job_id=? ORDER BY id", (job_id,)
            )
            return [json.loads(row[0]) for row in rows]

    def prepare_attempt(
        self, job: Any, data: dict[str, Any], broker_next_id: int, client_id: int
    ) -> dict[str, Any]:
        with self.transaction() as db:
            return self._prepare_attempt_in(db, job, data, broker_next_id, client_id)

    def _prepare_attempt_in(
        self, db: Any, job: Any, data: dict[str, Any], broker_next_id: int, client_id: int
    ) -> dict[str, Any]:
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

    def cycle_for_entry(
        self,
        account: str,
        environment: str,
        cycle_id: str,
        snapshot: dict[str, Any],
        min_ratio: Decimal,
        *,
        profile_revision: str | None = None,
    ) -> dict[str, Any] | None:
        # A cycle captures our realized capital, never personal broker equity.
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM te_profiles WHERE account=? AND environment=? ORDER BY "
                "rowid DESC LIMIT 1",
                (account, environment),
            ).fetchone()
            if profile_revision:
                row = db.execute(
                    "SELECT payload FROM te_profiles WHERE revision=?", (profile_revision,)
                ).fetchone()
            profile = historical_profile(json.loads(row[0]))
            equity = CapitalLedger(self).equity(account, profile.account_type, db=db)
            row = db.execute(
                "SELECT payload FROM te_account_cycles WHERE account=? AND environment=? AND "
                "cycle_id=? AND status='ACTIVE'",
                (account, environment, cycle_id),
            ).fetchone()
            if row:
                value = json.loads(row[0])
                # Explicit allocation changes take effect for subsequent grants.
                principal = CapitalLedger(self).book(account, db=db)["principal"]
                value["equity"] = str(
                    decimal(value["equity"]) + principal - decimal(value["principal"])
                    if "principal" in value
                    else equity
                )
                value["principal"] = str(principal)
                db.execute(
                    "UPDATE te_account_cycles SET equity=?,payload=? WHERE account=? AND "
                    "environment=? AND cycle_id=?",
                    (value["equity"], encode(value), account, environment, cycle_id),
                )
                return value
            if profile.account_type == "MARGIN":
                if (
                    CapitalLedger.groups(db, account)
                    or db.execute(
                        "SELECT 1 FROM te_attempts WHERE account=? AND state NOT IN "
                        "('SETTLED','NOT_SENT') LIMIT 1",
                        (account,),
                    ).fetchone()
                ):
                    return None
            db.execute(
                "UPDATE te_account_cycles SET status='CLOSED' WHERE account=? AND environment=?"
                " AND status='ACTIVE'",
                (account, environment),
            )
            value = dict(
                account=account,
                environment=environment,
                cycle_id=cycle_id,
                equity=str(equity),
                principal=str(CapitalLedger(self).book(account, db=db)["principal"]),
                min_entry_notional_ratio=str(min_ratio),
                captured_at=self.journal.clock().isoformat(),
                broker_snapshot_at=snapshot["at"],
                status="ACTIVE",
            )
            db.execute(
                "INSERT OR REPLACE INTO te_account_cycles VALUES(?,?,?,?,?,?,?)",
                (
                    account,
                    environment,
                    cycle_id,
                    "ACTIVE",
                    str(equity),
                    value["captured_at"],
                    encode(value),
                ),
            )
            return value

    def reserve_attempt(self, job, data, cycle, broker_next_id, client_id, snapshot_at):
        from .planning import hedge_quantity, integer_pair

        with self.transaction() as db:
            current = json.loads(
                db.execute("SELECT payload FROM te_jobs WHERE id=?", (job["id"],)).fetchone()[0]
            )
            execution = json.loads(
                db.execute(
                    "SELECT payload FROM te_executions WHERE id=?", (job["execution_id"],)
                ).fetchone()[0]
            )
            profile = historical_profile(
                json.loads(
                    db.execute(
                        "SELECT payload FROM te_profiles WHERE revision=?",
                        (execution["profile_revision"],),
                    ).fetchone()[0]
                )
            )
            snap = db.execute(
                "SELECT payload FROM te_sync_state WHERE account=?", (job["account"],)
            ).fetchone()
            if not snap or json.loads(snap[0])["at"] != snapshot_at:
                return None, "BROKER_PORTFOLIO_STALE"
            if current["state"] in {"WAIT_MAIN", "DONE", "FAILED"}:
                return None, "DEPENDENCY_NOT_READY"
            from .configuration import active_in

            active = active_in(db, job["ticker"])
            if groups.entry_block_reason(
                execution,
                current,
                self.journal.clock(),
                active["configuration"] if active else None,
            ):
                return None, "POSITION_EXIT_REQUESTED"
            sizing = decimal(data["sizing_price"])
            fills = [
                f
                for f in self._effective_fills(db, job["account"])
                if f["attempt_id"]
                in {
                    r[0]
                    for r in db.execute("SELECT id FROM te_attempts WHERE job_id=?", (job["id"],))
                }
            ]
            spent = sum((decimal(f["quantity"]) * decimal(f["price"]) for f in fills), Decimal(0))
            filled_qty = sum(int(decimal(f["quantity"])) for f in fills)
            if not execution.get("budget_usd"):
                budget = CapitalLedger(self).capacity(
                    db, execution, profile, decimal(cycle["equity"]), json.loads(snap[0])
                )
                hedge = next((leg for leg in execution["legs"] if leg["role"] == "HEDGE"), None)
                # Commission allowance stays inside the slot/group grant.
                fee_reserve = max(Decimal(2 if hedge else 1), budget * Decimal(".001"))
                usable = max(Decimal(0), budget - fee_reserve)
                if (
                    profile.account_type == "MARGIN"
                    and usable
                    < decimal(cycle["equity"]) * profile.strategy.min_entry_notional_ratio
                ):
                    return None, "PORTFOLIO_CAPACITY_EXHAUSTED"
                if hedge:
                    try:
                        plan = integer_pair(
                            usable,
                            decimal(execution["trading_config"]["hedge_ratio"]),
                            sizing,
                            decimal(data["hedge_plan"]["sizing_price"]),
                        )
                    except ValueError as exc:
                        CapitalLedger(self).release_slots(db, job["account"])
                        return None, str(exc)
                else:
                    plan = dict(
                        main_quantity=int(usable // sizing), main_cap=str(usable), hedge_cap="0"
                    )
                if not plan["main_quantity"]:
                    CapitalLedger(self).release_slots(db, job["account"])
                    return None, "PORTFOLIO_CAPACITY_EXHAUSTED"
                effective = decimal(plan["main_quantity"]) * sizing + decimal(
                    plan.get("hedge_quantity", 0)
                ) * decimal(data.get("hedge_plan", {}).get("sizing_price", 0))
                if (
                    profile.account_type == "MARGIN"
                    and effective
                    < decimal(cycle["equity"]) * profile.strategy.min_entry_notional_ratio
                ):
                    return None, "PORTFOLIO_CAPACITY_EXHAUSTED"
                execution.update(
                    budget_usd=str(budget),
                    plan=plan,
                    hedge_plan=data.get("hedge_plan"),
                    group_state="OPENING",
                )
                db.execute(
                    "UPDATE te_executions SET payload=? WHERE id=?",
                    (encode(execution), execution["id"]),
                )
            plan = execution["plan"]
            if current.get("role", "MAIN") == "MAIN":
                cap, target = decimal(plan["main_cap"]), plan["main_quantity"]
            else:
                cap = decimal(plan["hedge_cap"])
                current["main_notional_usd"] = str(
                    groups.main_notional(self, db, job["execution_id"], job["account"])
                )
                try:
                    additional = hedge_quantity(
                        decimal(current["main_notional_usd"]),
                        cap,
                        decimal(execution["trading_config"]["hedge_ratio"]),
                        sizing,
                        spent,
                    )
                except ValueError as exc:
                    return None, str(exc)
                if additional == 0:
                    return None, "HEDGE_TARGET_SATISFIED"
                current["target_quantity"] = filled_qty + additional
                target = current["target_quantity"]
            quantity = min(target - filled_qty, int(max(Decimal(0), cap - spent) // sizing))
            if quantity < 1:
                return None, "PORTFOLIO_CAPACITY_EXHAUSTED"
            current.update(
                grant_usd=str(cap),
                reserved_open_usd=str(max(Decimal(0), cap - spent)),
                cycle_id=cycle["cycle_id"],
            )
            db.execute("UPDATE te_jobs SET payload=? WHERE id=?", (encode(current), current["id"]))
            attempt = self._prepare_attempt_in(
                db,
                current,
                {
                    **data,
                    "quantity": quantity,
                    "cycle_id": cycle["cycle_id"],
                    "main_notional_usd": current.get("main_notional_usd"),
                },
                broker_next_id,
                client_id,
            )
            return attempt, None

    def _job_open_notional(self, db: Any, job: dict[str, Any]) -> Decimal:
        fill_prices = {
            fill["exec_id"]: decimal(fill["price"])
            for fill in self._effective_fills(db, job["account"])
        }
        return sum(
            (
                decimal(quantity) * fill_prices[fill_id]
                for fill_id, quantity in db.execute(
                    "SELECT fill_id,qty FROM te_allocations WHERE lot_id=? AND kind='ENTRY'",
                    (job.get("leg_id", job["execution_id"]),),
                )
                if fill_id in fill_prices
            ),
            Decimal(0),
        )

    def _entry_position_notional(self, db: Any, job: dict[str, Any]) -> Decimal:
        row = db.execute(
            "SELECT payload FROM te_lots WHERE id=?", (job["execution_id"],)
        ).fetchone()
        if not row:
            return Decimal(0)
        lot = json.loads(row[0])
        if not lot["remaining_qty"]:
            return Decimal(0)
        entry_qty = sum(
            (
                decimal(qty)
                for (qty,) in db.execute(
                    "SELECT qty FROM te_allocations WHERE lot_id=? AND kind='ENTRY'",
                    (job.get("leg_id", job["execution_id"]),),
                )
            ),
            Decimal(0),
        )
        return (
            self._job_open_notional(db, job) * decimal(lot["remaining_qty"]) / entry_qty
            if entry_qty
            else Decimal(0)
        )

    def void_prepared_attempt(self, attempt: dict[str, Any], *, if_prepared: bool = False) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM te_attempts WHERE id=?", (attempt["id"],)
            ).fetchone()
            current = json.loads(row[0])
            if current["state"] != "PREPARED":
                if if_prepared:
                    return
                raise ValueError("attempt is not safe to release")
            current["state"] = "NOT_SENT"
            db.execute(
                "UPDATE te_attempts SET state='NOT_SENT',payload=? WHERE id=?",
                (encode(current), current["id"]),
            )
            job = json.loads(
                db.execute(
                    "SELECT payload FROM te_jobs WHERE id=?", (current["job_id"],)
                ).fetchone()[0]
            )
            job["reserved_open_usd"] = "0"
            db.execute("UPDATE te_jobs SET payload=? WHERE id=?", (encode(job), job["id"]))

    def cleanup_prepared(self):
        with self.transaction(write=False) as db:
            rows = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT a.payload FROM te_attempts a JOIN te_jobs j ON j.id=a.job_id WHERE a.state='PREPARED' AND j.state IN ('DONE','FAILED')"
                )
            ]
        for attempt in rows:
            self.void_prepared_attempt(attempt, if_prepared=True)
        return len(rows)

    def mark_submitting(self, attempt: dict[str, Any], *, wait_seconds: float) -> dict | None:
        """Final atomic entry admission before the external broker call."""
        from .recovery import ENTRY_FAILURE_LIMIT

        with self.transaction() as db:
            value = json.loads(
                db.execute(
                    "SELECT payload FROM te_attempts WHERE id=?", (attempt["id"],)
                ).fetchone()[0]
            )
            job = json.loads(
                db.execute("SELECT payload FROM te_jobs WHERE id=?", (value["job_id"],)).fetchone()[
                    0
                ]
            )
            if value["state"] != "PREPARED" or job["state"] in {"DONE", "FAILED"}:
                return None
            competing = db.execute(
                "SELECT 1 FROM te_attempts WHERE account=? AND id!=? AND state NOT IN "
                "('PREPARED','SETTLED','NOT_SENT') AND "
                "json_extract(payload,'$.contract.con_id')=? LIMIT 1",
                (job["account"], value["id"], value["contract"]["con_id"]),
            ).fetchone()
            if competing:
                return None
            if job["leg"] == "EXIT":
                row = db.execute(
                    "SELECT payload FROM te_lots WHERE id=?",
                    (job.get("leg_id", job["execution_id"]),),
                ).fetchone()
                lot = json.loads(row[0]) if row else None
                if (
                    not lot
                    or lot.get("unattributed_qty")
                    or value["quantity"] > lot["remaining_qty"]
                ):
                    return None
                if job.get("role") == "HEDGE":
                    main = db.execute(
                        "SELECT payload FROM te_lots WHERE id=?", (job["execution_id"],)
                    ).fetchone()
                    busy = db.execute(
                        "SELECT 1 FROM te_attempts a JOIN te_jobs j ON j.id=a.job_id WHERE "
                        "j.execution_id=? AND "
                        "COALESCE(json_extract(j.payload,'$.role'),'MAIN')='MAIN' AND a.state "
                        "NOT IN ('SETTLED','NOT_SENT') LIMIT 1",
                        (job["execution_id"],),
                    ).fetchone()
                    if (main and json.loads(main[0])["remaining_qty"]) or busy:
                        return None
            if job["leg"] == "ENTRY":
                from doxagent.exit_strategy.repository import entry_blocked

                from .recovery import entry_queue_expired

                execution = json.loads(
                    db.execute(
                        "SELECT payload FROM te_executions WHERE id=?", (job["execution_id"],)
                    ).fetchone()[0]
                )
                if job.get("role") == "HEDGE":
                    main = db.execute(
                        "SELECT state FROM te_jobs WHERE id=?", (job["execution_id"] + ":entry",)
                    ).fetchone()
                    actual = groups.main_notional(self, db, job["execution_id"], job["account"])
                    if (
                        not main
                        or main[0] not in {"DONE", "FAILED"}
                        or not actual
                        or actual != decimal(value.get("main_notional_usd", 0))
                    ):
                        return None
                from .configuration import active_in

                active = active_in(db, job["ticker"])
                from doxagent.semantic_clock import semantic_day

                if active and semantic_day(self.journal.clock()).isoformat() in active[
                    "configuration"
                ].get("banned_semantic_dates", []):
                    job.update(
                        entry_abandoned_at=self.journal.clock().isoformat(),
                        entry_abandon_reason="BANNED_TRADE_DAY",
                    )
                    groups.save_job(db, job)
                    return None
                if groups.entry_block_reason(
                    execution,
                    job,
                    self.journal.clock(),
                    active["configuration"] if active else None,
                ) or entry_queue_expired(execution, self.journal.clock()):
                    return None
                if job.get("exit_requested_at") or entry_blocked(db, job["account"], job["ticker"]):
                    return None
                recovery = db.execute(
                    "SELECT payload FROM runtime_values "
                    "WHERE namespace='execution_recovery' AND key=?",
                    (job["account"],),
                ).fetchone()
                account_failures = json.loads(recovery[0]).get("failures", 0) if recovery else 0
                if (
                    job.get("entry_abandoned_at")
                    or max(job.get("entry_failures", 0), job.get("failures", 0), account_failures)
                    >= ENTRY_FAILURE_LIMIT
                ):
                    return None
            value.update(state="SUBMITTING", sent_at=self.journal.clock().isoformat())
            job.update(
                state="ACTIVE",
                failures=0,
                due_at=(self.journal.clock() + timedelta(seconds=wait_seconds)).isoformat(),
            )
            db.execute(
                "UPDATE te_attempts SET state=?,payload=? WHERE id=?",
                (value["state"], encode(value), value["id"]),
            )
            db.execute(
                "UPDATE te_jobs SET state=?,due_at=?,payload=? WHERE id=?",
                (job["state"], job["due_at"], encode(job), job["id"]),
            )
        return value

    def update_attempt(self, attempt: dict[str, Any], **updates: Any) -> dict[str, Any]:
        with self.transaction() as db:
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
        # Off-loop broker callbacks retain the durable write window.
        with self.journal.transaction() as db:
            db.execute(
                "INSERT INTO te_events(account,kind,payload,created_at) VALUES(?,?,?,?)",
                (account, kind, encode(payload), self.journal.clock().isoformat()),
            )

    def apply_order(self, account: str, event: dict[str, Any]) -> None:
        with self.transaction() as db:
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
            if status == "Rejected" or (status == "Inactive" and event.get("error")):
                value["rejection"] = value.get("rejection") or {
                    "status": status,
                    "error": event.get("error") or value.get("broker_error"),
                }
            db.execute("UPDATE te_attempts SET payload=? WHERE id=?", (encode(value), value["id"]))

    def apply_error(self, account: str, event: dict[str, Any]) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM te_attempts WHERE account=? AND client_id=? AND order_id=?",
                (account, event.get("client_id"), event.get("order_id")),
            ).fetchone()
            if not row:
                return
            value = json.loads(row[0])
            value.setdefault("broker_errors", []).append(event)
            if event.get("code") == 10329:
                value["precaution_rejection"] = event
            db.execute("UPDATE te_attempts SET payload=? WHERE id=?", (encode(value), value["id"]))

    def apply_fill(self, fill: dict[str, Any], exit_at: str) -> bool:
        qty = decimal(fill["quantity"])
        if qty < 0 or qty != int(qty):
            raise ValueError("NON_INTEGER_FILL")
        with self.transaction() as db:
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
                if job["leg"] == "ENTRY" and job.get("grant_usd"):
                    used = self._job_open_notional(db, job)
                    job["reserved_open_usd"] = (
                        "0"
                        if job["state"] == "DONE"
                        else str(max(Decimal(0), decimal(job["grant_usd"]) - used))
                    )
                    db.execute("UPDATE te_jobs SET payload=? WHERE id=?", (encode(job), job["id"]))
                if (
                    job["leg"] == "ENTRY"
                    and job["state"] == "DONE"
                    and job.get("role", "MAIN") == "MAIN"
                ):
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
                    if not execution["filled_qty"]:
                        execution.update(entry_result="FAILED", entry_reason="CORRECTED_NO_FILL")
                        job.update(result="FAILED", reason="CORRECTED_NO_FILL")
                        db.execute(
                            "UPDATE te_jobs SET payload=? WHERE id=?", (encode(job), job["id"])
                        )
                    elif execution.get("ownership_version") == 2:
                        target = execution.get("plan", {}).get("main_quantity", 0)
                        result = (
                            "FILLED"
                            if target and execution["filled_qty"] >= target
                            else "PARTIAL_FILLED"
                        )
                        execution.update(
                            entry_result=result, entry_reason="EFFECTIVE_FILL_RECONCILED"
                        )
                        job.update(result=result, reason=execution["entry_reason"])
                        db.execute(
                            "UPDATE te_jobs SET payload=? WHERE id=?", (encode(job), job["id"])
                        )
                    elif job.get("cycle_id"):
                        cycle_row = db.execute(
                            "SELECT equity,payload FROM te_account_cycles "
                            "WHERE account=? AND cycle_id=?",
                            (job["account"], job["cycle_id"]),
                        ).fetchone()
                        if cycle_row:
                            minimum = decimal(cycle_row[0]) * decimal(
                                json.loads(cycle_row[1])["min_entry_notional_ratio"]
                            )
                            opened = self._entry_position_notional(db, job)
                            execution["entry_result"] = (
                                "FILLED" if opened >= minimum else "PARTIAL_FILLED"
                            )
                            execution["entry_reason"] = (
                                "LATE_FILL_RECONCILED"
                                if opened >= minimum
                                else "BELOW_MIN_AFTER_FILL"
                                if opened
                                else "OFFSET_ONLY"
                            )
                    elif execution["filled_qty"] and execution.get("entry_result") == "FAILED":
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
        with self.transaction(write=False) as db:
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
            execution = json.loads(
                db.execute(
                    "SELECT payload FROM te_executions WHERE id=?", (job["execution_id"],)
                ).fetchone()[0]
            )
            virtual = execution.get("ownership_version") == 2
            leg_id = job.get("leg_id", job["execution_id"])
            if job.get("role") == "HEDGE":
                main_row = db.execute(
                    "SELECT payload FROM te_lots WHERE id=?", (job["execution_id"],)
                ).fetchone()
                if main_row:
                    fill = {**fill, "exit_at": json.loads(main_row[0])["scheduled_exit"]}
            quantity = int(decimal(fill["quantity"]))
            if quantity == 0:
                if job["leg"] == "ENTRY":
                    lots.setdefault(
                        leg_id,
                        dict(
                            id=leg_id,
                            account=account,
                            con_id=con_id,
                            ticker=job.get("traded_ticker", job["ticker"]),
                            origin_ticker=job["ticker"],
                            group_id=job["execution_id"],
                            role=job.get("role", "MAIN"),
                            ownership_version=2 if virtual else 1,
                            sign=1 if fill["side"] == "BUY" else -1,
                            remaining_qty=0,
                            first_fill_at=fill["time"],
                            scheduled_exit=fill["exit_at"],
                        ),
                    )
                continue
            sign = 1 if fill["side"] == "BUY" else -1
            if job["leg"] == "EXIT":
                lot = lots.get(leg_id)
                if not lot or sign == lot["sign"]:
                    raise ValueError("LOT_ALLOCATION_GAP")
                used = min(quantity, lot["remaining_qty"])
                lot["remaining_qty"] -= used
                allocations.append((fill["exec_id"], lot["id"], str(used), "EXIT"))
                if quantity > used:
                    lot["unattributed_qty"] = lot.get("unattributed_qty", 0) + quantity - used
                    from .capital import put

                    put(
                        db,
                        "execution_event_gaps",
                        "excess:" + fill["exec_id"],
                        dict(
                            account=account,
                            con_id=con_id,
                            error="EXIT_OVERFILL_AFTER_CORRECTION",
                            quantity=quantity - used,
                        ),
                    )
                continue
            for lot in sorted(lots.values(), key=lambda v: (v["first_fill_at"], v["id"])):
                if (
                    virtual
                    or lot.get("ownership_version") == 2
                    or lot["sign"] == sign
                    or quantity == 0
                ):
                    continue
                used = min(quantity, lot["remaining_qty"])
                lot["remaining_qty"] -= used
                quantity -= used
                if used:
                    allocations.append((fill["exec_id"], lot["id"], str(used), "FIFO_OFFSET"))
            if quantity:
                identity = leg_id
                lot = lots.setdefault(
                    identity,
                    {
                        "id": identity,
                        "account": account,
                        "con_id": con_id,
                        "ticker": job.get("traded_ticker", job["ticker"]),
                        "origin_ticker": job["ticker"],
                        "group_id": job["execution_id"],
                        "role": job.get("role", "MAIN"),
                        "ownership_version": 2 if virtual else 1,
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
                if job["state"] in {"ACTIVE", "RECONCILE_REQUIRED"}:
                    job["due_at"] = self.journal.clock().isoformat()
                    groups.save_job(db, job)
                else:
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
                        "execution_id": lot.get("group_id", lot["id"]),
                        "leg_id": lot["id"],
                        "role": lot.get("role", "MAIN"),
                        "traded_ticker": lot["ticker"],
                        "account": account,
                        "ticker": lot.get("origin_ticker", lot["ticker"]),
                        "leg": "EXIT",
                        "state": "WAIT_MAIN" if lot.get("role") == "HEDGE" else "READY",
                        "due_at": lot["scheduled_exit"],
                        "generation": 0,
                        "failures": 0,
                    },
                )

        from doxagent.exit_strategy.repository import refresh_books

        refresh_books(db, account, con_id, self.journal.clock)
        for identity in {lot.get("group_id", lot["id"]) for lot in lots.values()}:
            groups.advance(self, identity, db=db)

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
        with self.transaction() as db:
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

    def finish(self, job, reason, *, complete=False, disabled=False):
        fills = self.fills(job["id"])
        quantity = sum(int(decimal(f["quantity"])) for f in fills)
        if job["leg"] == "ENTRY":
            result = (
                "DIRECTION_DISABLED"
                if disabled
                else "FILLED"
                if quantity and complete
                else "PARTIAL_FILLED"
                if quantity
                else "FAILED"
            )
            with self.transaction() as db:
                value = json.loads(
                    db.execute(
                        "SELECT payload FROM te_executions WHERE id=?", (job["execution_id"],)
                    ).fetchone()[0]
                )
                prefix = "" if job.get("role", "MAIN") == "MAIN" else "hedge_"
                if prefix:
                    value["hedge_status"] = "COMPLETE" if result == "FILLED" else "HEDGE_INCOMPLETE"

                value.update(
                    {
                        prefix + "entry_result": result,
                        prefix + "entry_reason": reason,
                        prefix + "filled_qty": quantity,
                        prefix + "filled_notional": str(
                            sum(
                                (decimal(f["quantity"]) * decimal(f["price"]) for f in fills),
                                Decimal(0),
                            )
                        ),
                    }
                )
                db.execute(
                    "UPDATE te_executions SET payload=? WHERE id=?", (encode(value), value["id"])
                )
                current = json.loads(
                    db.execute("SELECT payload FROM te_jobs WHERE id=?", (job["id"],)).fetchone()[0]
                )
                current.update(state="DONE", result=result, reason=reason, reserved_open_usd="0")
                groups.save_job(db, current)
                groups.advance(self, job["execution_id"], db=db)
            return current
        lot = self.get("lots", job.get("leg_id", job["execution_id"]))
        remaining = lot["remaining_qty"] if lot else 0
        result = "EXIT_FILLED" if not remaining else "EXIT_PARTIAL" if quantity else "EXIT_FAILED"
        if remaining:
            self.gap(job, result, reason)
        current = self.update_job(
            job, state="DONE" if not remaining else "FAILED", result=result, reason=reason
        )
        groups.advance(self, job["execution_id"])
        return current

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

    def recover_failed_exits(self) -> int:
        """Resume transient/remaining exit obligations without idle writer ownership."""
        from .recovery import transient

        with self.transaction(write=False) as db:
            identities = [
                r[0]
                for r in db.execute("SELECT id FROM te_jobs WHERE leg='EXIT' AND state='FAILED'")
            ]
        recovered = 0
        for identity in identities:
            with self.transaction() as db:
                row = db.execute(
                    "SELECT j.payload,l.payload FROM te_jobs j JOIN te_lots l ON "
                    "l.id=COALESCE(json_extract(j.payload,'$.leg_id'),j.execution_id) WHERE "
                    "j.id=? AND j.state='FAILED'",
                    (identity,),
                ).fetchone()
                if row is None:
                    continue
                job, lot = map(json.loads, row)
                if not (
                    decimal(lot["remaining_qty"]) > 0
                    and (
                        job.get("exit_requested_at")
                        or datetime.fromisoformat(lot["scheduled_exit"]) <= self.journal.clock()
                    )
                ):
                    continue
                is_transient = transient(RuntimeError(job.get("reason", "")))
                hedge_rejection = (
                    job.get("role") == "HEDGE" and job.get("reason") == "BROKER_REJECTED"
                )
                retry_remaining = job.get("reason") == "RETRY_BUDGET_EXHAUSTED" or hedge_rejection
                if not is_transient and not retry_remaining:
                    continue
                if retry_remaining:
                    attempts = [
                        json.loads(r[0])
                        for r in db.execute(
                            "SELECT payload FROM te_attempts WHERE job_id=?", (job["id"],)
                        )
                    ]
                    if any(a["state"] not in {"SETTLED", "NOT_SENT"} for a in attempts):
                        continue
                    rejections = [a["rejection"] for a in attempts if a.get("rejection")]
                    permanent = any(
                        word in json.dumps(rejections).lower()
                        for word in (
                            "permission",
                            "not allowed",
                            "account mismatch",
                            "invalid contract",
                            "insufficient",
                            "margin",
                            "precaution",
                        )
                    )
                    if rejections and (not hedge_rejection or permanent):
                        continue
                    latest = max(
                        (
                            datetime.fromisoformat(a["settled_at"])
                            for a in attempts
                            if a.get("settled_at")
                        ),
                        default=self.journal.clock(),
                    )
                    if (self.journal.clock() - latest).total_seconds() < 60:
                        continue
                    job["generation"] = job.get("generation", 0) + 1
                job.update(
                    state="READY" if retry_remaining else "RECONCILE_REQUIRED",
                    due_at=self.journal.clock().isoformat(),
                    recovery_reason="REMAINING_EXIT_NEXT_ROUND"
                    if retry_remaining
                    else "TRANSIENT_EXIT_AUTO_RECOVERY",
                    recovered_at=self.journal.clock().isoformat(),
                )
                db.execute(
                    "UPDATE te_jobs SET state=?,due_at=?,payload=? WHERE id=?",
                    (job["state"], job["due_at"], encode(job), job["id"]),
                )
                recovered += 1
        return recovered

    def allocate_order_id(self, account: Any, client_id: Any, suggested: Any) -> Any:
        key = f"{account}:{client_id}"
        with self.transaction() as db:
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
