"""Restartable local control saga. No research or broker calls run in this service."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from cdecr.config import CDECRSettings
from doxagent.cdecr_integration.prebuilt import (
    CDECRPrebuiltStore,
    PrebuiltError,
    configuration_fingerprint,
)
from doxagent.message_bus_v2.schema import TickerMonitoringStatus, UpdateActor
from doxagent.runtime_scheduler.schema import TickerRunStatus
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.catalog import default_plan
from doxagent.ticker_initialization.schema import RunStatus

from .mirror import apply, states_in
from .repository import ControlError, ControlRepository


class ControlService:
    def __init__(
        self,
        repository: ControlRepository,
        initialization: Any,
        bus: Any,
        scheduler_repository: Any,
        settings: DoxAgentSettings | None = None,
    ) -> None:
        self.repository, self.initialization = repository, initialization
        self.bus, self.scheduler = bus, scheduler_repository
        self.settings = settings or DoxAgentSettings()
        self._heartbeat_at = None
        self._report_at = self.repository.journal.clock()
        self._mirror_writes = 0

    def _mirrors(self, state: dict[str, Any], op: dict[str, Any], *, snapshot=None) -> None:
        initial, bus, acknowledged = snapshot if snapshot is not None else (None, None, False)
        if initial != state or (snapshot is None and op["kind"] == "REMOVE"):
            with self.initialization._write() as db:
                apply(db, state)
                self._mirror_writes += 1
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
        if bus != state:
            with self.bus.repository.transaction() as db:
                apply(db, state)
                self._mirror_writes += 1
        if not acknowledged:
            self._ack(state, "initializer")

    def _ack(self, state: dict[str, Any], consumer: str) -> None:
        from doxagent.persistent_runtime_v2.journal import encode

        with self.repository.journal.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO v2_control_ack VALUES(?,?,?,?)",
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
            body = op["body"]
            if body.get("_internal_repair_resume"):
                self.initialization.resume_for_repair(
                    identity,
                    incident_id=body["repair_incident_id"],
                    round_id=body["repair_round_id"],
                    control_epoch=state["epoch"],
                    control_operation_id=op["id"],
                )
            else:
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
            prebuilt = None
            store = None
            if self.settings.cdecr_execution_mode in {"LOCAL_OR_PREBUILT", "PREBUILT_REQUIRED"}:
                store = CDECRPrebuiltStore(self.settings.cdecr_prebuilt_root)
                try:
                    prebuilt = store.claim_ready(
                        market="US",
                        ticker=ticker,
                        operation_id=op["id"],
                        compatibility_version=self.settings.cdecr_prebuilt_compatibility_version,
                        fingerprint=configuration_fingerprint(CDECRSettings()),
                        max_age_hours=self.settings.cdecr_prebuilt_max_age_hours,
                    )
                except PrebuiltError as exc:
                    raise ControlError(exc.code) from exc
                except OSError as exc:
                    raise RuntimeError("CDECR prebuilt store is temporarily unavailable") from exc
                if prebuilt is None and self.settings.cdecr_execution_mode == "PREBUILT_REQUIRED":
                    raise ControlError("CDECR_PREBUILT_REQUIRED")
            reference, manifest = prebuilt if prebuilt is not None else (None, None)
            try:
                run = self.initialization.submit(
                    ticker,
                    (
                        manifest.research_cutoff_at
                        if manifest is not None
                        else datetime.fromisoformat(cutoff)
                        if cutoff
                        else self.repository.journal.clock()
                    ),
                    default_plan(
                        cdecr_prebuilt_ref=(
                            reference.model_dump(mode="json") if reference is not None else None
                        ),
                        o4_delivery_enabled=(
                            self.settings.ticker_initialization_o4_delivery_enabled
                        ),
                    ),
                    reinitialize=bool(active),
                    control_operation_id=op["id"],
                    control_epoch=state["epoch"],
                )
            except Exception:
                if store is not None and reference is not None:
                    if self.initialization.by_control_operation(op["id"]) is None:
                        try:
                            store.release_claim(op["id"])
                        except OSError as exc:
                            raise RuntimeError(
                                "CDECR prebuilt claim could not be safely released"
                            ) from exc
                raise
            self.repository.settle(op["id"], initialization_id=run.initialization_id)
            effective = self.repository.get(ticker)
            if effective is None:
                raise RuntimeError("control state disappeared after initialization submission")
            self._mirrors(effective, op)
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
        effective = self.repository.get(ticker)
        if effective is None:
            raise RuntimeError("control state disappeared after activation")
        self._mirrors(effective, op)
        return True

    def tick(self) -> int:
        completed = 0
        now = self.repository.journal.clock()
        if self._heartbeat_at is None or (now - self._heartbeat_at).total_seconds() >= 10:
            self.repository.journal.set("v2_workers", "control", {"heartbeat_at": now.isoformat()})
            self._heartbeat_at = now
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
        if (now - self._report_at).total_seconds() >= 60:
            logging.getLogger(__name__).info(
                "control_reconciliation mirror_write_transactions=%s", self._mirror_writes
            )
            self._mirror_writes = 0
            self._report_at = now
        return completed

    def reconcile_initializations(self) -> None:
        with self.repository.read() as db:
            rows = db.execute("SELECT payload FROM v2_ticker_control").fetchall()
            acknowledgements = {
                (r[0], r[1])
                for r in db.execute(
                    "SELECT ticker,epoch FROM v2_control_ack WHERE consumer='initializer'"
                )
            }
        states = [json.loads(row[0]) for row in rows]
        if not states:
            return
        runs, heads, initial_mirrors = self.initialization.control_reconciliation_snapshot(
            [s["initialization_id"] for s in states if s.get("initialization_id")]
        )
        with self.bus.repository._connect() as db:
            bus_mirrors = states_in(db)
        for state in states:
            run_id = state.get("initialization_id")
            run = runs.get(run_id) if state.get("admission_allowed") else None
            active_id = heads.get(state["ticker"])
            if (
                run
                and run["status"] == RunStatus.FAILED
                and run["control_epoch"] == state["epoch"]
                and not state.get("initialization_failed")
            ):
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
                        state = latest
            elif (
                run
                and run["status"] == RunStatus.SUCCEEDED
                and run["control_epoch"] == state["epoch"]
                and active_id
                and not (state.get("activation_id") == active_id and state["analysis_allowed"])
            ):
                active = self.initialization.active_revision(state["ticker"])
                if (
                    active
                    and active["artifacts"]["monitoring_configuration"]["initialization_id"]
                    == run_id
                ):
                    self.repository.activate_candidate(
                        state["ticker"], state["epoch"], active["revision_id"]
                    )
                    state = self.repository.get(state["ticker"]) or state
            self._mirrors(
                state,
                {"kind": "REMOVE" if state.get("removed") else "START", "id": None},
                snapshot=(
                    initial_mirrors.get(state["ticker"]),
                    bus_mirrors.get(state["ticker"]),
                    (state["ticker"], state["epoch"]) in acknowledgements,
                ),
            )
