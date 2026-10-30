"""Own strategy capital and two CASH slots; broker equity never sets the budget."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from decimal import Decimal

from doxagent.persistent_runtime_v2.journal import encode
from doxagent.semantic_clock import boundary, semantic_day

from .strategy import decimal

ZERO = Decimal(0)


def initialize(db):
    statements = """
        CREATE TABLE IF NOT EXISTS te_capital_events (
            id TEXT PRIMARY KEY,account TEXT NOT NULL,kind TEXT NOT NULL,
            amount TEXT NOT NULL,effective_at TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS te_capital_events_account ON te_capital_events(account,effective_at);
        CREATE TABLE IF NOT EXISTS te_cash_slots (
            account TEXT NOT NULL,slot INTEGER NOT NULL,ticker TEXT,
            PRIMARY KEY(account,slot),UNIQUE(account,ticker));
    """
    for statement in statements.split(";"):
        if statement.strip():
            db.execute(statement)


def put(db, namespace, key, value):
    db.execute(
        "INSERT OR REPLACE INTO runtime_values VALUES(?,?,?)", (namespace, key, encode(value))
    )


def get(db, namespace, key, default=None):
    row = db.execute(
        "SELECT payload FROM runtime_values WHERE namespace=? AND key=?", (namespace, key)
    ).fetchone()
    return json.loads(row[0]) if row else default


class CapitalLedger:
    def __init__(self, repo):
        self.repo = repo

    def allocate(self, account, amount, identity, reason, *, actor="local-admin"):
        amount = decimal(amount)
        if amount < 0 or not reason.strip():
            raise ValueError("invalid capital allocation")
        with self.repo.transaction() as db:
            prior = db.execute(
                "SELECT payload FROM te_capital_events WHERE id=?", (identity,)
            ).fetchone()
            if prior:
                value = json.loads(prior[0])
                if value["account"] != account or decimal(value["target"]) != amount:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return value
            current = sum(
                (
                    decimal(r[0])
                    for r in db.execute(
                        "SELECT amount FROM te_capital_events WHERE account=? AND "
                        "kind='ALLOCATION'",
                        (account,),
                    )
                ),
                ZERO,
            )
            value = {"account": account, "target": str(amount), "actor": actor, "reason": reason}
            if not db.execute(
                "SELECT 1 FROM te_capital_events WHERE account=? LIMIT 1", (account,)
            ).fetchone():
                value["opening_cash_offset"] = str(
                    -sum(
                        (
                            decimal(json.loads(r[0]).get("remaining_qty", 0))
                            * json.loads(r[0])["sign"]
                            * self._lot_cost(db, json.loads(r[0])["id"])
                            for r in db.execute(
                                "SELECT payload FROM te_lots WHERE account=?", (account,)
                            )
                        ),
                        ZERO,
                    )
                )
            db.execute(
                "INSERT INTO te_capital_events VALUES(?,?,?,?,?,?)",
                (
                    identity,
                    account,
                    "ALLOCATION",
                    str(amount - current),
                    self.repo.journal.clock().isoformat(),
                    encode(value),
                ),
            )
            return value

    def adjust(self, account, amount, identity, reason, effective_at):
        amount = decimal(amount)
        at = datetime.fromisoformat(effective_at)
        if not reason.strip() or at.tzinfo is None or at > self.repo.journal.clock():
            raise ValueError("dated capital adjustment and reason required")
        value = dict(account=account, amount=str(amount), reason=reason, effective_at=effective_at)
        with self.repo.transaction() as db:
            old = db.execute(
                "SELECT payload FROM te_capital_events WHERE id=?", (identity,)
            ).fetchone()
            if old and json.loads(old[0]) != value:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            db.execute(
                "INSERT OR IGNORE INTO te_capital_events VALUES(?,?,?,?,?,?)",
                (identity, account, "ADJUSTMENT", str(amount), effective_at, encode(value)),
            )
        return value

    @staticmethod
    def _lot_cost(db, identity):
        values = [
            (decimal(q), decimal(json.loads(payload)["price"]))
            for q, payload in db.execute(
                "SELECT a.qty,f.payload FROM te_allocations a JOIN te_fills f ON "
                "f.account=a.account AND f.exec_id=a.fill_id WHERE a.lot_id=? AND "
                "a.kind='ENTRY'",
                (identity,),
            )
        ]
        quantity = sum((q for q, _ in values), ZERO)
        return sum((q * p for q, p in values), ZERO) / quantity if quantity else ZERO

    def book(self, account, *, before=None, db=None):
        if db is None:
            with self.repo.transaction(write=False) as conn:
                return self.book(account, before=before, db=conn)
        events = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM te_capital_events WHERE account=? ORDER BY effective_at,id",
                (account,),
            )
        ]
        allocations = [r for r in events if r["kind"] == "ALLOCATION"]
        if not allocations:
            raise ValueError("STRATEGY_CAPITAL_UNCONFIGURED")
        inception = min(r["effective_at"] for r in allocations)
        principal = sum((decimal(r["amount"]) for r in allocations), ZERO)
        offset = sum(
            (decimal(json.loads(r["payload"]).get("opening_cash_offset", 0)) for r in allocations),
            ZERO,
        )
        queues = {}
        realized, cash, cash_debits = ZERO, principal + offset, ZERO
        fills = self.repo._effective_fills(db, account)
        attempts = {
            r["id"]: json.loads(r["payload"])
            for r in db.execute("SELECT id,payload FROM te_attempts WHERE account=?", (account,))
        }
        jobs = {
            r["id"]: json.loads(r["payload"])
            for r in db.execute("SELECT id,payload FROM te_jobs WHERE account=?", (account,))
        }
        fees = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT key,payload FROM runtime_values WHERE namespace='execution_fees' AND "
                "key LIKE ?",
                (account + ":%",),
            )
        }
        attempt_totals = {}
        for fill in fills:
            attempt_totals[fill["attempt_id"]] = attempt_totals.get(
                fill["attempt_id"], ZERO
            ) + decimal(fill["quantity"])
        pending = False
        for fill in sorted(fills, key=lambda f: (f["time"], f["exec_id"])):
            if before and datetime.fromisoformat(fill["time"]) >= before:
                continue
            qty, price = decimal(fill["quantity"]), decimal(fill["price"])
            if not qty:
                continue
            job = jobs[attempts[fill["attempt_id"]]["job_id"]]
            leg = job.get("leg_id", job["execution_id"])
            queue = queues.setdefault(leg, deque())
            sign = 1 if fill["side"] == "BUY" else -1
            live = fill["time"] >= inception
            fee = fees.get(account + ":" + fill["exec_id"])
            if fee and fee.get("currency") == "USD":
                cost = decimal(fee["commission"])
            else:
                pending = pending or live
                total = attempt_totals[fill["attempt_id"]]
                cost = (
                    min(max(total * Decimal(".005"), Decimal(1)), total * price * Decimal(".01"))
                    * qty
                    / total
                )
            cash_debits += (qty * price if sign == 1 else ZERO) + max(ZERO, cost)
            if live:
                cash -= qty * price * sign + cost
                realized -= cost
            if job["leg"] == "ENTRY":
                queue.append([qty, price, sign])
            else:
                remaining = qty
                while remaining and queue:
                    part = queue[0]
                    used = min(remaining, part[0])
                    if live:
                        realized += used * (price - part[1]) * part[2]
                    part[0] -= used
                    remaining -= used
                    if not part[0]:
                        queue.popleft()
                pending = pending or bool(remaining)
        for event in events:
            if event["kind"] == "ALLOCATION" or event["effective_at"] < inception:
                continue
            if before and datetime.fromisoformat(event["effective_at"]) >= before:
                continue
            realized += decimal(event["amount"])
            cash += decimal(event["amount"])
            cash_debits += max(ZERO, -decimal(event["amount"]))
        return {
            "principal": principal,
            "realized": realized,
            "equity": max(ZERO, principal + realized),
            "cash": cash,
            "cash_debits": cash_debits,
            "provisional": pending,
        }

    def unrealized(self, account, *, db):
        lots = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM te_lots WHERE account=?", (account,))
        ]
        if any(lot.get("unattributed_qty") for lot in lots):
            return None
        if not any(lot["remaining_qty"] for lot in lots):
            return ZERO
        snapshot = db.execute(
            "SELECT payload FROM te_sync_state WHERE account=?", (account,)
        ).fetchone()
        if not snapshot:
            return None
        prices = {
            str(v["con_id"]): decimal(v["market_price"])
            for v in json.loads(snapshot[0]).get("portfolio", [])
            if v.get("market_price") is not None
        }
        result = ZERO
        for (payload,) in db.execute("SELECT payload FROM te_lots WHERE account=?", (account,)):
            lot = json.loads(payload)
            if not lot["remaining_qty"]:
                continue
            mark = prices.get(str(lot["con_id"]))
            if mark is None:
                return None
            result += (
                decimal(lot["remaining_qty"]) * lot["sign"] * (mark - self._lot_cost(db, lot["id"]))
            )
        return result

    def equity(self, account, account_type, *, db=None):
        before = (
            boundary(semantic_day(self.repo.journal.clock())) if account_type == "CASH" else None
        )
        return self.book(account, before=before, db=db)["equity"]

    @staticmethod
    def groups(db, account, exclude=None):
        values = []
        for (payload,) in db.execute(
            "SELECT payload FROM te_executions WHERE account=?", (account,)
        ):
            value = json.loads(payload)
            if value["id"] == exclude:
                continue
            if value.get("ownership_version") == 2:
                if value.get("budget_usd") and value.get("group_state") != "CLOSED":
                    values.append(value)
            else:
                row = db.execute(
                    "SELECT payload FROM te_lots WHERE id=?", (value["id"],)
                ).fetchone()
                lot = json.loads(row[0]) if row else None
                if lot and lot["remaining_qty"]:
                    values.append(
                        {
                            **value,
                            "budget_usd": str(
                                decimal(lot["remaining_qty"])
                                * CapitalLedger._lot_cost(db, lot["id"])
                            ),
                        }
                    )
        return values

    def capacity(self, db, execution, profile, equity, snapshot):
        account, ticker = execution["account"], execution["ticker"]
        groups = self.groups(db, account, execution["id"])
        locked = sum((decimal(g["budget_usd"]) for g in groups), ZERO)
        own = sum((decimal(g["budget_usd"]) for g in groups if g["ticker"] == ticker), ZERO)
        if profile.account_type == "MARGIN":
            return max(ZERO, min(equity - own, equity * Decimal("1.5") - locked))
        for slot in (0, 1):
            db.execute("INSERT OR IGNORE INTO te_cash_slots VALUES(?,?,NULL)", (account, slot))
        row = db.execute(
            "SELECT slot FROM te_cash_slots WHERE account=? AND ticker=?", (account, ticker)
        ).fetchone()
        if row is None:
            row = db.execute(
                "SELECT slot FROM te_cash_slots WHERE account=? AND ticker IS NULL ORDER BY "
                "slot LIMIT 1",
                (account,),
            ).fetchone()
            if row is None:
                return ZERO
            db.execute(
                "UPDATE te_cash_slots SET ticker=? WHERE account=? AND slot=?",
                (ticker, account, row[0]),
            )
        settled = snapshot.get("account_values", {}).get("SettledCash") or {}
        if settled.get("currency") != "USD":
            raise ValueError("SETTLED_USD_CASH_UNAVAILABLE")
        balance = decimal(settled["value"])
        # Keep own buys and fees reserved while the broker balance is unchanged.
        wallet = self.book(account, db=db)
        debits = wallet["cash_debits"]
        anchor = get(db, "execution_cash_anchors", account)
        if anchor is None or balance != decimal(anchor["balance"]):
            anchor = {
                "balance": str(balance),
                "debits": str(debits),
                "used": "0",
                "at": snapshot["at"],
            }
        used = max(
            ZERO,
            decimal(anchor.get("used", 0)),
            debits - decimal(anchor.get("debits", anchor.get("buys", debits))),
        )
        anchor["used"] = str(used)
        put(db, "execution_cash_anchors", account, anchor)
        balance -= used
        unspent = ZERO
        for group in groups:
            spent = sum(
                (
                    decimal(f["quantity"]) * decimal(f["price"])
                    for f in self.repo._effective_fills(db, account)
                    if f["side"] == "BUY" and f["attempt_id"].startswith(group["id"] + ":")
                ),
                ZERO,
            )
            unspent += max(ZERO, decimal(group["budget_usd"]) - spent)
        return max(
            ZERO,
            min(equity / 2 - own, equity - locked, wallet["cash"] - unspent, balance - unspent),
        )

    def release_slots(self, db, account):
        active = {g["ticker"] for g in self.groups(db, account)}
        for row in db.execute(
            "SELECT slot,ticker FROM te_cash_slots WHERE account=?", (account,)
        ).fetchall():
            if row[1] not in active:
                db.execute(
                    "UPDATE te_cash_slots SET ticker=NULL WHERE account=? AND slot=?",
                    (account, row[0]),
                )
