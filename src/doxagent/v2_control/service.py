"""Restartable local control saga. No research or broker calls run in this service."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from doxagent.message_bus_v2.schema import TickerMonitoringStatus, UpdateActor
from doxagent.runtime_scheduler.schema import TickerRunStatus
from doxagent.ticker_initialization.catalog import default_plan
from doxagent.ticker_initialization.schema import RunStatus

from .mirror import apply
from .repository import ControlError, ControlRepository


class ControlService:
    def __init__(
        self,
        repository: ControlRepository,
        initialization: Any,
        bus: Any,
        scheduler_repository: Any,
    ) -> None:
        self.repository, self.initialization = repository, initialization
        self.bus, self.scheduler = bus, scheduler_repository

    def _mirrors(self, state: dict[str, Any], op: dict[str, Any]) -> None:
        with self.initialization._write() as db:
            apply(db, state)
            if op["kind"] == "REMOVE":
                row = db.execute(
                    "SELECT run_id FROM ticker_operations WHERE ticker=?", (state["ticker"],)
                ).fetchone()
                if row:
                    run = self.initialization._run(db, row[0])
                    from doxagent.ticker_initialization.schema import NodeRecord

                    for node_row in db.execute(
                        "SELECT payload FROM initialization_nodes WHERE run_id=? "
                        "AND json_extract(payload,'$.status')='RUNNING'",
                        (row[0],),
                    ).fetchall():
                        node = NodeRecord.model_validate_json(node_row[0])
                        node.status, node.error = "FAILED", "OPERATOR_STOPPED"
                        self.initialization._save_node(db, row[0], node)
                    run.status, run.error, run.manual_resume_required = (
                        RunStatus.FAILED,
                        "OPERATOR_STOPPED",
                        False,
                    )
                    self.initialization._event(
                        db, run, "operator.stopped", {"operation_id": op["id"]}
                    )
                    db.execute("DELETE FROM ticker_operations WHERE run_id=?", (row[0],))
        with self.bus.repository.transaction() as db:
            apply(db, state)
        self._ack(state, "initializer")

    def _ack(self, state: dict[str, Any], consumer: str) -> None:
        from doxagent.persistent_runtime_v2.journal import encode

        with self.repository.journal.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO v2_control_ack VALUES(?,?,?,?)",
                (
                    state["ticker"],
                    state["epoch"],
                    consumer,
                    encode(
                        {
                            "operation_epoch": state["epoch"],
                            "mode": state["requested_mode"],
                            "at": self.repository.journal.clock().isoformat(),
                        }
                    ),
                ),
            )

    def step(self, op: dict[str, Any]) -> bool:
        state = self.repository.get(op["ticker"])
        if state is None or state["epoch"] != op["epoch"]:
            raise ControlError("OPERATION_SUPERSEDED")
        self._mirrors(state, op)
        ticker = op["ticker"]
        if op["kind"] in {"PAUSE", "REMOVE"}:
            if self.bus.repository.get_ticker_state(ticker):
                self.bus.set_ticker_status(
                    ticker,
                    TickerMonitoringStatus.STOPPED
                    if op["kind"] == "REMOVE"
                    else TickerMonitoringStatus.PAUSED,
                    actor=UpdateActor.SYSTEM,
                )
            self._ack(state, "bus")
            previous = self.scheduler.get_state(ticker)
            if previous:
                self.scheduler.upsert_state(
                    previous.model_copy(
                        update={
                            "status": TickerRunStatus.STOPPED
                            if op["kind"] == "REMOVE"
                            else TickerRunStatus.PAUSED,
                            "updated_at": self.repository.journal.clock(),
                        }
                    )
                )
            self._ack(state, "runtime")
            self.repository.settle(op["id"])
            return True
        if op["kind"] == "RESUME_INITIALIZATION":
            identity = op["body"]["initialization_id"]
            run = self.initialization.get(identity)
            if run.ticker != ticker:
                raise ControlError("RESOURCE_NOT_FOUND", 404)
            self.initialization.resume(
                identity,
                reason="V2 explicit resume",
                control_epoch=state["epoch"],
                control_operation_id=op["id"],
            )
            self.repository.settle(op["id"], initialization_id=identity)
            return True
        active = self.initialization.active_revision(ticker)
        if not active and op["body"].get("initialization") == "REUSE_ACTIVE":
            raise ControlError("NO_ACTIVE_REVISION")
        if op["body"].get("initialization") == "FORCE_INITIALIZE" or not active:
            cutoff = op["body"].get("research_cutoff_at")
            run = self.initialization.submit(
                ticker,
                datetime.fromisoformat(cutoff) if cutoff else self.repository.journal.clock(),
                default_plan(),
                reinitialize=bool(active),
                control_operation_id=op["id"],
                control_epoch=state["epoch"],
            )
            self.repository.settle(op["id"], initialization_id=run.initialization_id)
            self._mirrors(self.repository.get(ticker), op)
            return True
        identity = active["revision_id"]
        # Workers perform activation and report ACK themselves. No fabricated readiness.
        if not all(
            self.initialization.revision_acknowledged(ticker, identity, consumer)
            for consumer in ("bus", "runtime")
        ):
            return False
        bus_state, runtime_state = (
            self.bus.repository.get_ticker_state(ticker),
            self.scheduler.get_state(ticker),
        )
        if not bus_state or not runtime_state or bus_state.status != TickerMonitoringStatus.RUNNING:
            return False
        if runtime_state.status != TickerRunStatus.RUNNING:
            return False
        if runtime_state.metadata.get("v2_control_epoch") != state["epoch"] or (
            runtime_state.metadata.get("v2_control_mode") != state["requested_mode"]
        ):
            return False
        self._ack(state, "bus")
        self._ack(state, "runtime")
        self.repository.settle(op["id"], activation_id=identity)
        self._mirrors(self.repository.get(ticker), op)
        return True

    def tick(self) -> int:
        completed = 0
        now = self.repository.journal.clock()
        self.repository.journal.set("v2_workers", "control", {"heartbeat_at": now.isoformat()})
        for op in self.repository.pending():
            if datetime.fromisoformat(op["next_attempt_at"]) > now:
                continue
            op["state"] = "RUNNING"
            self.repository.update_operation(op)
            try:
                completed += self.step(op)
            except ControlError as exc:
                self.repository.fail(op["id"], exc.code)
            except (ValueError, KeyError, RuntimeError, sqlite3.OperationalError) as exc:
                op["attempts"] += 1
                op["error"] = type(exc).__name__
                op["next_attempt_at"] = (
                    now + timedelta(seconds=min(300, 2 ** min(op["attempts"], 8)))
                ).isoformat()
                self.repository.update_operation(op)
        self.reconcile_initializations()
        return completed

    def reconcile_initializations(self) -> None:
        with self.repository.journal.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM v2_ticker_control WHERE "
                "json_extract(payload,'$.initialization_id') IS NOT NULL "
                "AND json_extract(payload,'$.admission_allowed')=1"
            ).fetchall()
        import json

        for row in rows:
            state = json.loads(row[0])
            run = self.initialization.get(state["initialization_id"])
            active = self.initialization.active_revision(state["ticker"])
            if run.status == RunStatus.FAILED and run.control_epoch == state["epoch"]:
                from .repository import control_in

                with self.repository.journal.transaction() as db:
                    latest = control_in(db, state["ticker"])
                    if (
                        latest
                        and latest["epoch"] == state["epoch"]
                        and not latest.get("initialization_failed")
                    ):
                        latest.update(initialization_failed=True, initialization_incomplete=True)
                        latest["revision"] += 1
                        self.repository._save(db, latest, "initialization.failed")
                continue
            if (
                run.status != RunStatus.SUCCEEDED
                or run.control_epoch != state["epoch"]
                or not active
                or (
                    active["artifacts"]["monitoring_configuration"]["initialization_id"]
                    != run.initialization_id
                )
            ):
                continue
            self.repository.activate_candidate(
                state["ticker"], state["epoch"], active["revision_id"]
            )
            effective = self.repository.get(state["ticker"])
            if effective:
                self._mirrors(effective, {"kind": "START", "id": run.control_operation_id})
