"""Exit state and existing EXIT jobs share one atomic Runtime transaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from math import isfinite

from doxagent.persistent_runtime_v2.journal import digest, encode

from .schema import EXIT_REASONS, ExitPolicy


def initialize(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS ts_policies (revision TEXT PRIMARY KEY,payload TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS ts_positions (id TEXT PRIMARY KEY,account TEXT,"
        "phase TEXT,version INTEGER NOT NULL,payload TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS ts_positions_active ON ts_positions(phase,account)")


def policy_pin(db, account, environment, ticker):
    for key in (f"{account}:{environment}:{ticker}", f"{account}:{environment}:*"):
        row = db.execute(
            "SELECT payload FROM runtime_values WHERE namespace='exit_policy_bindings' AND key=?",
            (key,),
        ).fetchone()
        if row:
            return json.loads(row[0]).get("revision")
    return None


def _put(db, value, version):
    payload = {
        k: v
        for k, v in value.items()
        if k not in {"state_version", "publish", "request_reason", "evidence"}
    }
    db.execute(
        "INSERT INTO ts_positions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "phase=excluded.phase,version=excluded.version,payload=excluded.payload",
        (value["position_id"], value["account"], value["phase"], version, encode(payload)),
    )


def refresh_books(db, account, con_id, clock):
    """Called only on committed ledger mutations, not on the five-second loop."""
    from doxagent.trade_execution.repository import ExecutionRepository

    fills = {f["exec_id"]: f for f in ExecutionRepository._effective_fills(db, account, con_id)}
    for row in db.execute(
        "SELECT leg.payload,e.payload FROM te_lots leg JOIN te_executions e "
        "ON e.id=leg.id WHERE leg.account=? AND leg.con_id=?",
        (account, con_id),
    ).fetchall():
        lot, execution = map(json.loads, row)
        revision = execution.get("exit_policy_revision")
        if not revision:
            continue
        old_row = db.execute(
            "SELECT payload,version FROM ts_positions WHERE id=?", (lot["id"],)
        ).fetchone()
        old = json.loads(old_row[0]) if old_row else None
        if not old and not lot["remaining_qty"]:
            continue
        cash = notional = fees = 0.0
        ids = []
        allocated = {}
        rows = db.execute(
            "SELECT fill_id,qty,kind FROM te_allocations WHERE account=? AND con_id=? AND lot_id=?",
            (account, con_id, lot["id"]),
        ).fetchall()
        for fill_id, raw_qty, kind in rows:
            f = fills.get(fill_id)
            if f is None:
                continue
            qty, price = float(raw_qty), float(f["price"])
            cash += (-1 if kind == "ENTRY" else 1) * lot["sign"] * qty * price
            if kind == "ENTRY":
                notional += qty * price
            ids.append(fill_id)
            allocated.setdefault(f["attempt_id"], []).append((f, qty))
        for attempt_id, parts in allocated.items():
            all_fills = [f for f in fills.values() if f["attempt_id"] == attempt_id]
            total_qty = sum(float(f["quantity"]) for f in all_fills)
            estimated = min(
                max(1.0, total_qty * 0.005),
                sum(float(f["quantity"]) * float(f["price"]) for f in all_fills) * 0.01,
            )
            for f, qty in parts:
                actual = db.execute(
                    "SELECT payload FROM runtime_values WHERE namespace='execution_fees' AND key=?",
                    (account + ":" + f["exec_id"],),
                ).fetchone()
                if actual:
                    v = json.loads(actual[0])
                    amount = v.get("commission", v.get("commission_usd"))
                    if (
                        amount is not None
                        and v.get("currency", "USD") == "USD"
                        and isfinite(float(amount))
                        and abs(float(amount)) < 1e10
                    ):
                        fees += float(amount) * qty / float(f["quantity"])
                        continue
                fees += estimated * qty / total_qty if total_qty else 0.0
        correction = bool(old and not set(old.get("fill_ids", [])).issubset(ids))
        if old and old.get("valid_observations"):
            observed = datetime.fromisoformat(old["updated_at"])
            correction = correction or any(
                datetime.fromisoformat(fills[ident]["time"]) < observed
                for ident in set(ids) - set(old.get("fill_ids", []))
            )
        adjustments = any(r[2] == "ADJUSTMENT" for r in rows)
        book = dict(
            quantity=lot["sign"] * int(lot["remaining_qty"]),
            cash=cash,
            fees=fees,
            entry_notional=notional,
            lot_id=lot["id"],
            con_id=con_id,
            ticker=lot["ticker"],
            sign=lot["sign"],
            scheduled_exit=lot["scheduled_exit"],
        )
        fingerprint = digest(dict(book=book, fills=sorted(ids)))
        if old and old.get("book_version") == fingerprint:
            continue
        value = {
            **(old or {}),
            "position_id": lot["id"],
            "account": account,
            "profile_revision": execution["profile_revision"],
            "policy_revision": revision,
            "first_fill_at": lot["first_fill_at"],
            "phase": (old or {}).get("phase", "TRACKING"),
            "data_health": "STATE_GAP"
            if correction or adjustments
            else (old or {}).get("data_health", "WARMING_ATR"),
            "basis_invalid": correction or adjustments or (old or {}).get("basis_invalid", False),
            "legs": [book],
            "notional": notional,
            "fill_ids": sorted(ids),
            "book_version": fingerprint,
            "updated_at": clock().isoformat(),
        }
        if old and old["phase"] == "CLOSED" and lot["remaining_qty"] and not old.get("group_id"):
            # A late fill after flat is an exit obligation, not a fresh signal.
            value["phase"] = "EXIT_REQUESTED" if old.get("exit_requested_at") else "TRACKING"
            value["pending_reason"] = "RESIDUAL_GROUP_EXIT"
        _put(db, value, old_row[1] + 1 if old_row else 0)
        if value.get("group_id"):
            group_row = db.execute(
                "SELECT payload,version FROM ts_positions WHERE id=?", (value["group_id"],)
            ).fetchone()
            if group_row:
                group = json.loads(group_row[0])
                group["legs"] = [
                    book if leg["lot_id"] == lot["id"] else leg for leg in group["legs"]
                ]
                if lot["id"] == group["main_lot"]:
                    group["notional"] = notional
                group["book_version"] = digest(group["legs"])
                group["basis_invalid"] = group.get("basis_invalid", False) or value["basis_invalid"]
                reduced = old and abs(book["quantity"]) < abs(old["legs"][0]["quantity"])
                if reduced and group["phase"] != "EXIT_REQUESTED":
                    group["pending_reason"] = "RESIDUAL_GROUP_EXIT"
                if group["phase"] == "CLOSED" and any(leg["quantity"] for leg in group["legs"]):
                    group["phase"] = (
                        "EXIT_REQUESTED" if group.get("exit_requested_at") else "TRACKING"
                    )
                if any(leg["quantity"] == 0 for leg in group["legs"]) and any(
                    leg["quantity"] for leg in group["legs"]
                ):
                    group["pending_reason"] = "RESIDUAL_GROUP_EXIT"
                _put(db, group, group_row[1] + 1)


def entry_blocked(db, account, ticker):
    return (
        db.execute(
            "SELECT 1 FROM te_jobs WHERE account=? AND ticker=? AND leg='EXIT' "
            "AND json_extract(payload,'$.exit_requested_at') IS NOT NULL "
            "AND state!='DONE' LIMIT 1",
            (account, ticker),
        ).fetchone()
        is not None
    )


class ExitRepository:
    def __init__(self, journal):
        self.journal = journal

    def heartbeat(self, value):
        with self.journal.transaction(timeout=0.2) as db:
            db.execute(
                "INSERT OR REPLACE INTO runtime_values VALUES('v2_workers','exit_strategy',?)",
                (encode(value),),
            )

    def import_policy(self, policy: ExitPolicy):
        payload = policy.model_dump(mode="json")
        revision = "xp_" + digest(payload)[:24]
        with self.journal.transaction(timeout=0.2) as db:
            db.execute("INSERT OR IGNORE INTO ts_policies VALUES(?,?)", (revision, encode(payload)))
        return revision

    def bind(self, account, environment, revision, *, ticker="*", reason):
        if environment not in {"PAPER", "LIVE"} or not reason.strip():
            raise ValueError("environment and reason required")
        with self.journal.transaction(timeout=0.2) as db:
            if (
                revision
                and not db.execute(
                    "SELECT 1 FROM ts_policies WHERE revision=?", (revision,)
                ).fetchone()
            ):
                raise ValueError("unknown exit policy")
            if not db.execute(
                "SELECT 1 FROM te_profiles WHERE account=? AND environment=?",
                (account, environment),
            ).fetchone():
                raise ValueError("ACCOUNT_ENVIRONMENT_MISMATCH")
            db.execute(
                "INSERT OR REPLACE INTO runtime_values VALUES('exit_policy_bindings',?,?)",
                (
                    f"{account}:{environment}:{ticker}",
                    encode(
                        dict(
                            revision=revision,
                            reason=reason,
                            updated_at=self.journal.clock().isoformat(),
                        )
                    ),
                ),
            )

    def policy(self, revision):
        with self.journal.transaction(write=False, timeout=0.2) as db:
            row = db.execute(
                "SELECT payload FROM ts_policies WHERE revision=?", (revision,)
            ).fetchone()
        if not row:
            raise ValueError("unknown exit policy")
        return ExitPolicy.model_validate_json(row[0])

    def positions(self, *, include_closed=False):
        with self.journal.transaction(write=False, timeout=0.2) as db:
            rows = db.execute(
                "SELECT payload,version FROM ts_positions"
                + ("" if include_closed else " WHERE phase!='CLOSED'")
            ).fetchall()
        return [{**json.loads(row[0]), "state_version": row[1]} for row in rows]

    def unsettled(self, value):
        with self.journal.transaction(write=False, timeout=0.2) as db:
            return any(
                db.execute(
                    "SELECT 1 FROM te_attempts a JOIN te_jobs j ON j.id=a.job_id "
                    "WHERE j.execution_id=? AND a.state NOT IN ('SETTLED','NOT_SENT') LIMIT 1",
                    (leg["lot_id"],),
                ).fetchone()
                for leg in value["legs"]
            )

    def pause(self, position_id, paused, reason):
        if not reason.strip():
            raise ValueError("reason required")
        with self.journal.transaction(timeout=0.2) as db:
            row = db.execute(
                "SELECT payload,version FROM ts_positions WHERE id=?", (position_id,)
            ).fetchone()
            if not row:
                raise ValueError("unknown managed position")
            value = json.loads(row[0])
            value.update(paused=paused, pause_reason=reason)
            _put(db, value, row[1] + 1)

    def save_batch(self, updates):
        """CAS against fill updates; one short transaction per observation batch."""
        saved = set()
        with self.journal.transaction(timeout=0.2) as db:
            for value in updates:
                row = db.execute(
                    "SELECT version FROM ts_positions WHERE id=?", (value["position_id"],)
                ).fetchone()
                if not row or row[0] != value["state_version"]:
                    continue
                db.execute("SAVEPOINT ts_position_update")
                try:
                    version = row[0] + 1
                    _put(db, value, version)
                    saved.add(value["position_id"])
                    if value.get("request_reason"):
                        requested = self._request_in(
                            db,
                            value["position_id"],
                            value["request_reason"],
                            self.journal.clock().isoformat(),
                            version,
                            value.get("evidence"),
                        )
                        value.update(requested)
                        version += 1
                    elif value.get("publish"):
                        self._publish(db, value)
                    value["state_version"] = version
                    db.execute("RELEASE ts_position_update")
                except ValueError as exc:
                    db.execute("ROLLBACK TO ts_position_update")
                    db.execute("RELEASE ts_position_update")
                    saved.discard(value["position_id"])
                    logging.getLogger(__name__).error(
                        "EXIT_REQUEST_PERSIST_FAILED position=%s error=%s",
                        value["position_id"],
                        exc,
                    )
        return saved

    @staticmethod
    def _publish(db, value):
        summary = {
            k: value.get(k)
            for k in (
                "position_id",
                "phase",
                "data_health",
                "policy_revision",
                "first_fill_at",
                "notional",
                "pnl",
                "mfe",
                "atr",
                "stop_net",
                "activated_at",
                "exit_requested_at",
                "exit_reason",
                "updated_at",
            )
        }
        for key in ("first_fill_at", "exit_requested_at", "updated_at"):
            if summary.get(key):
                summary[key] = (
                    datetime.fromisoformat(summary[key])
                    .astimezone(UTC)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
        if summary["activated_at"] is not None:
            summary["activated_at"] = (
                datetime.fromtimestamp(summary["activated_at"], UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        summary["remaining_quantity"] = sum(abs(leg["quantity"]) for leg in value["legs"])
        summary["group_status"] = "SINGLE_LEG" if len(value["legs"]) == 1 else "EXPLICIT_GROUP"
        summary["valid_observations"] = value.get("valid_observations", 0)
        summary["invalid_observations"] = value.get("invalid_observations", 0)
        summary["confirmation"] = value.get("confirmation", [])
        for leg in value["legs"]:
            row = db.execute(
                "SELECT payload FROM te_executions WHERE id=?", (leg["lot_id"],)
            ).fetchone()
            if row:
                execution = json.loads(row[0])
                execution["exit_strategy"] = summary
                db.execute(
                    "UPDATE te_executions SET payload=? WHERE id=?",
                    (encode(execution), leg["lot_id"]),
                )

    def request_exit(self, position_id, reason, *, expected_version=None, evidence=None):
        if reason not in EXIT_REASONS:
            raise ValueError("unknown exit reason")
        now = self.journal.clock().isoformat()
        with self.journal.transaction(timeout=0.2) as db:
            return self._request_in(db, position_id, reason, now, expected_version, evidence)

    def _request_in(self, db, position_id, reason, now, expected_version=None, evidence=None):
        row = db.execute(
            "SELECT payload,version FROM ts_positions WHERE id=?", (position_id,)
        ).fetchone()
        if not row:
            raise ValueError("unknown managed position")
        value = json.loads(row[0])
        if value.get("exit_requested_at"):
            return value
        if expected_version is not None and row[1] != expected_version:
            raise ValueError("EXIT_STATE_VERSION_CHANGED")
        if value["phase"] == "CLOSED":
            return value
        value.update(
            phase="EXIT_REQUESTED",
            exit_requested_at=now,
            exit_reason=reason,
            updated_at=now,
            exit_evidence=evidence or {},
        )
        for leg in value["legs"]:
            lot_row = db.execute(
                "SELECT payload FROM te_lots WHERE id=?", (leg["lot_id"],)
            ).fetchone()
            if not lot_row or json.loads(lot_row[0])["remaining_qty"] != abs(leg["quantity"]):
                raise ValueError("EXIT_STATE_VERSION_CHANGED")
            job_row = db.execute(
                "SELECT payload FROM te_jobs WHERE id=?", (leg["lot_id"] + ":exit",)
            ).fetchone()
            if not job_row:
                if leg["quantity"]:
                    raise ValueError("EXIT_JOB_MISSING")
                continue
            job = json.loads(job_row[0])
            job.update(exit_requested_at=now, exit_reason=reason, position_id=position_id)
            if job["state"] not in {"ACTIVE", "RECONCILE_REQUIRED", "DONE", "FAILED"}:
                job.update(state="READY", due_at=min(job["due_at"], now))
            db.execute(
                "UPDATE te_jobs SET state=?,due_at=?,payload=? WHERE id=?",
                (job["state"], job["due_at"], encode(job), job["id"]),
            )
            entry_row = db.execute(
                "SELECT payload FROM te_jobs WHERE id=?", (leg["lot_id"] + ":entry",)
            ).fetchone()
            if entry_row:
                entry = json.loads(entry_row[0])
                if entry["state"] not in {"DONE", "FAILED"}:
                    entry.update(exit_requested_at=now, due_at=now)
                    db.execute(
                        "UPDATE te_jobs SET due_at=?,payload=? WHERE id=?",
                        (now, encode(entry), entry["id"]),
                    )
        _put(db, value, row[1] + 1)
        self._publish(db, value)
        db.execute(
            "INSERT INTO te_events(account,kind,payload,created_at) VALUES(?,?,?,?)",
            (
                value["account"],
                "exit_decision",
                encode({k: v for k, v in value.items() if k != "price_window"}),
                now,
            ),
        )
        return value

    def group(self, position_id, lot_ids, *, main_lot, reason):
        """Explicit execution-side group adapter; never discovers ETF positions."""
        if (
            len(set(lot_ids)) != len(lot_ids)
            or len(lot_ids) < 2
            or main_lot not in lot_ids
            or not reason.strip()
        ):
            raise ValueError("explicit distinct legs and main_lot required")
        with self.journal.transaction(timeout=0.2) as db:
            values = []
            for ident in lot_ids:
                row = db.execute("SELECT payload FROM ts_positions WHERE id=?", (ident,)).fetchone()
                if not row:
                    raise ValueError("GROUP_EXECUTION_NOT_CONNECTED")
                v = json.loads(row[0])
                if (
                    v["phase"] != "TRACKING"
                    or v.get("valid_observations", 0)
                    or v.get("mfe", 0)
                    or v.get("group_id")
                ):
                    raise ValueError("group legs must be attached before price observation")
                values.append(v)
            if (
                len({v["account"] for v in values}) != 1
                or len({v["policy_revision"] for v in values}) != 1
            ):
                raise ValueError("group account/policy mismatch")
            if db.execute("SELECT 1 FROM ts_positions WHERE id=?", (position_id,)).fetchone():
                raise ValueError("group identity already exists")
            value = dict(next(v for v in values if v["position_id"] == main_lot))
            value.update(
                position_id=position_id,
                group_id=position_id,
                main_lot=main_lot,
                first_fill_at=min(v["first_fill_at"] for v in values),
                legs=[leg for v in values for leg in v["legs"]],
                group_reason=reason,
            )
            for v in values:
                v.update(phase="CLOSED", group_id=position_id)
                db.execute(
                    "UPDATE ts_positions SET phase='CLOSED',version=version+1,payload=? WHERE id=?",
                    (encode(v), v["position_id"]),
                )
            _put(db, value, 0)
