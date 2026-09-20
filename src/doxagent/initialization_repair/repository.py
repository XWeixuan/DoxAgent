"""Transactional incident ledger stored beside initialization truth."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import uuid4

from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.schema import (
    InitializationError,
    NodeRecord,
    RunStatus,
    utc_now,
)

from .schema import (
    IncidentPhase,
    IncidentStatus,
    IssueEntry,
    NodeBudget,
    RepairIncident,
    RepairRound,
    RoundStatus,
)


class RepairRepository:
    """Small CAS-oriented facade; all writes use the initialization DB transaction."""

    def __init__(self, initialization: InitializationRepository) -> None:
        self.initialization = initialization

    @staticmethod
    def _incident(row: Any) -> RepairIncident:
        return RepairIncident.model_validate_json(row[0])

    @staticmethod
    def _round(row: Any) -> RepairRound:
        return RepairRound.model_validate_json(row[0])

    def get(self, incident_id: str) -> RepairIncident:
        with self.initialization._connection() as db:
            row = db.execute(
                "SELECT payload FROM initialization_repair_incidents WHERE id=?",
                (incident_id,),
            ).fetchone()
        if row is None:
            raise KeyError(incident_id)
        return self._incident(row)

    def for_initialization(self, initialization_id: str) -> RepairIncident | None:
        with self.initialization._connection() as db:
            row = db.execute(
                "SELECT payload FROM initialization_repair_incidents WHERE initialization_id=?",
                (initialization_id,),
            ).fetchone()
        return None if row is None else self._incident(row)

    def incidents(self, *, include_terminal: bool = True) -> list[RepairIncident]:
        query = "SELECT payload FROM initialization_repair_incidents"
        params: tuple[object, ...] = ()
        if not include_terminal:
            query += " WHERE status='ACTIVE'"
        query += " ORDER BY created_at,id"
        with self.initialization._connection() as db:
            return [self._incident(row) for row in db.execute(query, params)]

    def failed_candidates(self, *, created_after: datetime | None = None) -> list[str]:
        query = (
            "SELECT r.id FROM initialization_runs r "
            "LEFT JOIN initialization_repair_incidents i ON i.initialization_id=r.id "
            "WHERE json_extract(r.payload,'$.status')='FAILED' "
            "AND COALESCE(json_extract(r.payload,'$.error'),'')<>'OPERATOR_STOPPED' "
            "AND i.id IS NULL"
        )
        params: list[object] = []
        if created_after is not None:
            query += " AND json_extract(r.payload,'$.created_at')>=?"
            params.append(created_after.isoformat())
        query += " ORDER BY r.rowid"
        with self.initialization._connection() as db:
            return [str(row[0]) for row in db.execute(query, params)]

    def open_incident(
        self,
        initialization_id: str,
        *,
        expected_state_seq: int,
        source_image_id: str | None = None,
        source_revision: str | None = None,
        source_hash: str | None = None,
    ) -> RepairIncident:
        with self.initialization._write() as db:
            prior = db.execute(
                "SELECT payload FROM initialization_repair_incidents WHERE initialization_id=?",
                (initialization_id,),
            ).fetchone()
            if prior is not None:
                return self._incident(prior)
            run = self.initialization._run(db, initialization_id)
            if run.status is not RunStatus.FAILED or run.state_seq != expected_state_seq:
                raise InitializationError("FAILED_INITIALIZATION_CHANGED")
            if run.error == "OPERATOR_STOPPED":
                raise InitializationError("OPERATOR_STOPPED")
            if db.execute(
                "SELECT 1 FROM ticker_operations WHERE ticker=?", (run.ticker,)
            ).fetchone():
                raise InitializationError("DUPLICATE_ACTIVE_INITIALIZATION")
            incident = RepairIncident(
                incident_id=f"repair-{uuid4().hex}",
                initialization_id=initialization_id,
                ticker=run.ticker,
                source_image_id=source_image_id,
                source_revision=source_revision,
                source_hash=source_hash,
            )
            db.execute(
                "INSERT INTO initialization_repair_incidents"
                "(id,initialization_id,ticker,status,phase,created_at,updated_at,payload) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    incident.incident_id,
                    initialization_id,
                    run.ticker,
                    incident.status,
                    incident.phase,
                    incident.created_at.isoformat(),
                    incident.updated_at.isoformat(),
                    incident.model_dump_json(),
                ),
            )
            return incident

    def failed_targets(self, initialization_id: str) -> list[str]:
        nodes = self.initialization.nodes(initialization_id)
        failed = [node for node in nodes if node.status == "FAILED"]
        parents_with_failed_children = {
            str(node.inputs["managed_by"]) for node in failed if node.inputs.get("managed_by")
        }
        return [node.key for node in failed if node.key not in parents_with_failed_children]

    def start_round(
        self,
        incident_id: str,
        *,
        failed_state_seq: int,
        target_nodes: Iterable[str] | None = None,
    ) -> RepairRound:
        with self.initialization._write() as db:
            incident_row = db.execute(
                "SELECT payload FROM initialization_repair_incidents WHERE id=?",
                (incident_id,),
            ).fetchone()
            if incident_row is None:
                raise KeyError(incident_id)
            incident = self._incident(incident_row)
            if incident.status is not IncidentStatus.ACTIVE:
                raise InitializationError("REPAIR_INCIDENT_NOT_ACTIVE")
            if incident.current_round_id:
                current = db.execute(
                    "SELECT payload FROM initialization_repair_rounds WHERE id=?",
                    (incident.current_round_id,),
                ).fetchone()
                if current is not None:
                    parsed = self._round(current)
                    if parsed.status not in {RoundStatus.FINISHED, RoundStatus.FAILED}:
                        return parsed
            run = self.initialization._run(db, incident.initialization_id)
            if run.status is not RunStatus.FAILED or run.state_seq != failed_state_seq:
                raise InitializationError("FAILED_INITIALIZATION_CHANGED")
            nodes = [
                NodeRecord.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_nodes WHERE run_id=? ORDER BY rowid",
                    (run.initialization_id,),
                )
            ]
            failed = [node for node in nodes if node.status == "FAILED"]
            parent_keys = {
                str(node.inputs["managed_by"]) for node in failed if node.inputs.get("managed_by")
            }
            defaults = [node.key for node in failed if node.key not in parent_keys]
            targets = list(dict.fromkeys(target_nodes or defaults))
            available = {node.key: node for node in failed}
            if not targets or any(key not in available for key in targets):
                raise InitializationError("REPAIR_TARGET_NOT_FAILED")
            evidence: dict[str, dict[str, object]] = {
                key: {
                    "state_seq": run.state_seq,
                    "generation": available[key].generation,
                    "ordinal": available[key].ordinal,
                    "error": available[key].error,
                }
                for key in targets
            }
            ordinals: dict[str, int] = {}
            exhausted: list[str] = []
            for key in targets:
                row = db.execute(
                    "SELECT payload FROM initialization_repair_node_budgets "
                    "WHERE incident_id=? AND node_key=?",
                    (incident_id, key),
                ).fetchone()
                parent_key = available[key].inputs.get("managed_by")
                if row is None and parent_key:
                    parent_row = db.execute(
                        "SELECT payload FROM initialization_repair_node_budgets "
                        "WHERE incident_id=? AND node_key=?",
                        (incident_id, str(parent_key)),
                    ).fetchone()
                    if parent_row is not None:
                        inherited = NodeBudget.model_validate_json(parent_row[0])
                        inherited.node_key = key
                        inherited.alias_of = str(parent_key)
                        db.execute(
                            "DELETE FROM initialization_repair_node_budgets "
                            "WHERE incident_id=? AND node_key=?",
                            (incident_id, str(parent_key)),
                        )
                        row = (inherited.model_dump_json(),)
                budget = (
                    NodeBudget.model_validate_json(row[0])
                    if row
                    else NodeBudget(
                        incident_id=incident_id,
                        node_key=key,
                        first_failure=evidence[key],
                        latest_failure=evidence[key],
                    )
                )
                if budget.rounds_started >= 3:
                    exhausted.append(key)
                    continue
                budget.rounds_started += 1
                budget.latest_failure = evidence[key]
                ordinals[key] = budget.rounds_started
                db.execute(
                    "INSERT INTO initialization_repair_node_budgets"
                    "(incident_id,node_key,rounds_started,payload) VALUES(?,?,?,?) "
                    "ON CONFLICT(incident_id,node_key) DO UPDATE SET "
                    "rounds_started=excluded.rounds_started,payload=excluded.payload",
                    (incident_id, key, budget.rounds_started, budget.model_dump_json()),
                )
            if exhausted:
                raise InitializationError("REPAIR_BUDGET_EXHAUSTED:" + ",".join(exhausted))
            seq = int(
                db.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM initialization_repair_rounds "
                    "WHERE incident_id=?",
                    (incident_id,),
                ).fetchone()[0]
            )
            repair_round = RepairRound(
                round_id=f"round-{uuid4().hex}",
                incident_id=incident_id,
                seq=seq,
                failed_state_seq=failed_state_seq,
                target_nodes=targets,
                node_ordinals=ordinals,
            )
            db.execute(
                "INSERT INTO initialization_repair_rounds"
                "(id,incident_id,seq,status,created_at,updated_at,payload) VALUES(?,?,?,?,?,?,?)",
                (
                    repair_round.round_id,
                    incident_id,
                    seq,
                    repair_round.status,
                    repair_round.created_at.isoformat(),
                    repair_round.updated_at.isoformat(),
                    repair_round.model_dump_json(),
                ),
            )
            incident.current_round_id = repair_round.round_id
            incident.phase = IncidentPhase.CODING
            self._save_incident(db, incident)
            return repair_round

    def round(self, round_id: str) -> RepairRound:
        with self.initialization._connection() as db:
            row = db.execute(
                "SELECT payload FROM initialization_repair_rounds WHERE id=?", (round_id,)
            ).fetchone()
        if row is None:
            raise KeyError(round_id)
        return self._round(row)

    def rounds(self, incident_id: str) -> list[RepairRound]:
        with self.initialization._connection() as db:
            return [
                self._round(row)
                for row in db.execute(
                    "SELECT payload FROM initialization_repair_rounds "
                    "WHERE incident_id=? ORDER BY seq",
                    (incident_id,),
                )
            ]

    def budgets(self, incident_id: str) -> list[NodeBudget]:
        with self.initialization._connection() as db:
            return [
                NodeBudget.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_repair_node_budgets "
                    "WHERE incident_id=? ORDER BY node_key",
                    (incident_id,),
                )
            ]

    def update_incident(self, incident_id: str, **changes: object) -> RepairIncident:
        with self.initialization._write() as db:
            row = db.execute(
                "SELECT payload FROM initialization_repair_incidents WHERE id=?", (incident_id,)
            ).fetchone()
            if row is None:
                raise KeyError(incident_id)
            incident = self._incident(row).model_copy(update=changes)
            incident.updated_at = utc_now()
            self._save_incident(db, incident)
            return incident

    def update_round(self, round_id: str, **changes: object) -> RepairRound:
        with self.initialization._write() as db:
            row = db.execute(
                "SELECT payload FROM initialization_repair_rounds WHERE id=?", (round_id,)
            ).fetchone()
            if row is None:
                raise KeyError(round_id)
            repair_round = self._round(row).model_copy(update=changes)
            repair_round.updated_at = utc_now()
            db.execute(
                "UPDATE initialization_repair_rounds "
                "SET status=?,updated_at=?,payload=? WHERE id=?",
                (
                    repair_round.status,
                    repair_round.updated_at.isoformat(),
                    repair_round.model_dump_json(),
                    round_id,
                ),
            )
            return repair_round

    def finish_round(self, round_id: str, *, result: str, exit_code: int | None) -> RepairRound:
        """Close one business execution and apply the per-node repair ceiling immediately."""

        with self.initialization._write() as db:
            row = db.execute(
                "SELECT payload FROM initialization_repair_rounds WHERE id=?", (round_id,)
            ).fetchone()
            if row is None:
                raise KeyError(round_id)
            repair_round = self._round(row)
            if repair_round.status in {RoundStatus.FINISHED, RoundStatus.FAILED}:
                return repair_round
            incident_row = db.execute(
                "SELECT payload FROM initialization_repair_incidents WHERE id=?",
                (repair_round.incident_id,),
            ).fetchone()
            if incident_row is None:
                raise KeyError(repair_round.incident_id)
            incident = self._incident(incident_row)
            run = self.initialization._run(db, incident.initialization_id)
            nodes = {
                node.key: node
                for node in (
                    NodeRecord.model_validate_json(item[0])
                    for item in db.execute(
                        "SELECT payload FROM initialization_nodes WHERE run_id=?",
                        (run.initialization_id,),
                    )
                )
            }
            crossed = [
                key
                for key in repair_round.target_nodes
                if key in nodes and nodes[key].status == "SUCCEEDED"
            ]
            for key in crossed:
                budget_row = db.execute(
                    "SELECT payload FROM initialization_repair_node_budgets "
                    "WHERE incident_id=? AND node_key=?",
                    (incident.incident_id, key),
                ).fetchone()
                if budget_row:
                    budget = NodeBudget.model_validate_json(budget_row[0])
                    if budget.crossed_at is None:
                        budget.crossed_at = utc_now()
                        budget.crossed_state_seq = run.state_seq
                        db.execute(
                            "UPDATE initialization_repair_node_budgets SET payload=? "
                            "WHERE incident_id=? AND node_key=?",
                            (budget.model_dump_json(), incident.incident_id, key),
                        )
            unresolved = [key for key in repair_round.target_nodes if key not in crossed]
            exhausted = []
            for key in unresolved:
                budget_row = db.execute(
                    "SELECT rounds_started FROM initialization_repair_node_budgets "
                    "WHERE incident_id=? AND node_key=?",
                    (incident.incident_id, key),
                ).fetchone()
                if budget_row and int(budget_row[0]) >= 3:
                    exhausted.append(key)
            repair_round.status = RoundStatus.FINISHED if not unresolved else RoundStatus.FAILED
            repair_round.result = result
            repair_round.exit_code = exit_code
            repair_round.updated_at = utc_now()
            db.execute(
                "UPDATE initialization_repair_rounds "
                "SET status=?,updated_at=?,payload=? WHERE id=?",
                (
                    repair_round.status,
                    repair_round.updated_at.isoformat(),
                    repair_round.model_dump_json(),
                    round_id,
                ),
            )
            if exhausted:
                incident.status = IncidentStatus.HUMAN_REQUIRED
                incident.phase = IncidentPhase.RECORD
                incident.last_error = "REPAIR_BUDGET_EXHAUSTED:" + ",".join(exhausted)
            elif run.status is RunStatus.SUCCEEDED:
                incident.status = IncidentStatus.SUCCEEDED
                incident.phase = IncidentPhase.RECORD
            else:
                incident.phase = IncidentPhase.CONTEXT
            self._save_incident(db, incident)
            return repair_round

    def mark_crossed(self, incident_id: str, node_keys: Iterable[str], state_seq: int) -> None:
        with self.initialization._write() as db:
            for key in node_keys:
                row = db.execute(
                    "SELECT payload FROM initialization_repair_node_budgets "
                    "WHERE incident_id=? AND node_key=?",
                    (incident_id, key),
                ).fetchone()
                if row is None:
                    continue
                budget = NodeBudget.model_validate_json(row[0])
                if budget.crossed_at is None:
                    budget.crossed_at = utc_now()
                    budget.crossed_state_seq = state_seq
                    db.execute(
                        "UPDATE initialization_repair_node_budgets SET payload=? "
                        "WHERE incident_id=? AND node_key=?",
                        (budget.model_dump_json(), incident_id, key),
                    )

    def release(self, incident_id: str, *, reason: str) -> RepairIncident:
        if not reason.strip():
            raise ValueError("release reason required")
        incident = self.get(incident_id)
        if incident.status not in {
            IncidentStatus.ACTIVE,
            IncidentStatus.HUMAN_REQUIRED,
        }:
            raise InitializationError("REPAIR_INCIDENT_ALREADY_TERMINAL")
        run = self.initialization.get(incident.initialization_id)
        if run.status is not RunStatus.FAILED:
            raise InitializationError("REPAIR_RELEASE_REQUIRES_FAILED")
        with self.initialization._connection() as db:
            if db.execute(
                "SELECT 1 FROM ticker_operations WHERE run_id=?", (run.initialization_id,)
            ).fetchone():
                raise InitializationError("REPAIR_EXECUTION_ACTIVE")
        return self.update_incident(
            incident_id,
            status=IncidentStatus.CANCELLED,
            phase=IncidentPhase.RECORD,
            last_error=f"released: {reason.strip()}",
        )

    def add_issue(self, entry: IssueEntry) -> bool:
        with self.initialization._write() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO initialization_repair_issue_entries"
                "(entry_id,incident_id,round_id,kind,created_at,payload) VALUES(?,?,?,?,?,?)",
                (
                    entry.entry_id,
                    entry.incident_id,
                    entry.round_id,
                    entry.kind,
                    entry.created_at.isoformat(),
                    entry.model_dump_json(),
                ),
            )
            return result.rowcount == 1

    def issues(self) -> list[IssueEntry]:
        with self.initialization._connection() as db:
            return [
                IssueEntry.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_repair_issue_entries "
                    "ORDER BY created_at,entry_id"
                )
            ]

    @staticmethod
    def _save_incident(db: Any, incident: RepairIncident) -> None:
        incident.updated_at = utc_now()
        db.execute(
            "UPDATE initialization_repair_incidents "
            "SET status=?,phase=?,updated_at=?,payload=? WHERE id=?",
            (
                incident.status,
                incident.phase,
                incident.updated_at.isoformat(),
                incident.model_dump_json(),
                incident.incident_id,
            ),
        )
