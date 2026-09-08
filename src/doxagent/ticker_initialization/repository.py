"""SQLite truth, short transactions, fencing, immutable executions and outbox."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .schema import (
    BudgetExhausted,
    InitializationError,
    Lease,
    LeaseLost,
    NodeRecord,
    NodeResult,
    NodeSpec,
    RunRecord,
    RunStatus,
    progress_phase,
    semantic_day,
    utc_now,
)


class InitializationRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS initialization_runs (
                    id TEXT PRIMARY KEY, ticker TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ticker_operations (
                    ticker TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
                    owner TEXT, token INTEGER NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS initialization_nodes (
                    run_id TEXT NOT NULL, node_key TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,node_key)
                );
                CREATE TABLE IF NOT EXISTS initialization_attempts (
                    execution_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    node_key TEXT NOT NULL, generation INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 2),
                    payload TEXT NOT NULL,
                    UNIQUE(run_id,node_key,generation,ordinal)
                );
                CREATE TABLE IF NOT EXISTS initialization_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v2_model_invocations (
                    id TEXT PRIMARY KEY,ticker TEXT,run_id TEXT,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS initialization_outbox (
                    run_id TEXT PRIMARY KEY, state_seq INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activation_revisions (
                    revision_id TEXT PRIMARY KEY, ticker TEXT NOT NULL,
                    base_revision TEXT, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ticker_active_revision (
                    ticker TEXT PRIMARY KEY, revision_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activation_worker_ack (
                    ticker TEXT NOT NULL, worker TEXT NOT NULL,
                    revision_id TEXT NOT NULL, heartbeat REAL NOT NULL,
                    PRIMARY KEY(ticker, worker)
                );
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def submit(
        self,
        ticker: str,
        cutoff: datetime,
        plan: list[NodeSpec],
        *,
        reinitialize: bool = False,
        operation_kind: str | None = None,
        expected_base_revision: str | None = None,
        control_operation_id: str | None = None,
        control_epoch: int | None = None,
    ) -> RunRecord:
        ticker = ticker.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker):
            raise ValueError("US ticker required")
        if cutoff.tzinfo is None:
            raise ValueError("research cutoff must include timezone")
        self._validate_plan(plan)
        with self._write() as db:
            if control_operation_id:
                prior = db.execute(
                    "SELECT payload FROM initialization_runs WHERE "
                    "json_extract(payload,'$.control_operation_id')=?",
                    (control_operation_id,),
                ).fetchone()
                if prior:
                    return RunRecord.model_validate_json(prior[0])
            if db.execute("SELECT 1 FROM ticker_operations WHERE ticker=?", (ticker,)).fetchone():
                raise InitializationError("DUPLICATE_ACTIVE_INITIALIZATION")
            active = db.execute(
                "SELECT revision_id FROM ticker_active_revision WHERE ticker=?", (ticker,)
            ).fetchone()
            if expected_base_revision is not None and (
                active is None or active[0] != expected_base_revision
            ):
                raise InitializationError("active revision changed before operator submission")
            previous = [
                RunRecord.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_runs WHERE ticker=?", (ticker,)
                )
            ]
            if not reinitialize and (
                active
                or any(
                    run.status == RunStatus.SUCCEEDED
                    and run.operation_kind in {"INITIALIZE", "REINITIALIZE"}
                    for run in previous
                )
            ):
                raise InitializationError("ALREADY_INITIALIZED")
            run = RunRecord(
                initialization_id=f"init-{ticker.lower()}-{uuid4().hex}",
                ticker=ticker,
                research_cutoff_at=cutoff,
                semantic_day=semantic_day(cutoff),
                base_revision=active[0] if active else None,
                control_epoch=control_epoch,
                control_operation_id=control_operation_id,
                operation_kind=operation_kind or ("REINITIALIZE" if reinitialize else "INITIALIZE"),
            )
            db.execute(
                "INSERT INTO initialization_runs VALUES (?,?,?)",
                (
                    run.initialization_id,
                    ticker,
                    run.model_dump_json(),
                ),
            )
            db.execute(
                "INSERT INTO ticker_operations(ticker,run_id) VALUES (?,?)",
                (
                    ticker,
                    run.initialization_id,
                ),
            )
            for spec in plan:
                self._save_node(db, run.initialization_id, NodeRecord(**spec.model_dump()))
            reason = plan[0].inputs.get("operator_reason") or plan[0].inputs.get("_operator_reason")
            self._event(
                db,
                run,
                "submitted",
                {"reason": reason, "actor": "local_operator"} if reason else {},
            )
            return run

    @staticmethod
    def _validate_plan(plan: list[NodeSpec]) -> None:
        keys = {node.key for node in plan}
        if not plan or len(keys) != len(plan):
            raise ValueError("plan must contain unique nodes")
        done: set[str] = set()
        while len(done) < len(keys):
            ready = {n.key for n in plan if set(n.dependencies) <= done} - done
            if not ready:
                raise ValueError("cyclic or missing node dependencies")
            done |= ready

    def get(self, run_id: str) -> RunRecord:
        with self._connection() as db:
            return self._run(db, run_id)

    @staticmethod
    def _run(db: sqlite3.Connection, run_id: str) -> RunRecord:
        row = db.execute("SELECT payload FROM initialization_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunRecord.model_validate_json(row[0])

    def nodes(self, run_id: str) -> list[NodeRecord]:
        with self._connection() as db:
            return [
                NodeRecord.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_nodes WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                )
            ]

    @staticmethod
    def _node(db: sqlite3.Connection, run_id: str, key: str) -> NodeRecord:
        row = db.execute(
            "SELECT payload FROM initialization_nodes WHERE run_id=? AND node_key=?", (run_id, key)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return NodeRecord.model_validate_json(row[0])

    @staticmethod
    def _save_node(db: sqlite3.Connection, run_id: str, node: NodeRecord) -> None:
        db.execute(
            """INSERT INTO initialization_nodes VALUES (?,?,?)
            ON CONFLICT(run_id,node_key) DO UPDATE SET payload=excluded.payload""",
            (
                run_id,
                node.key,
                node.model_dump_json(),
            ),
        )
        if node.execution_id:
            db.execute(
                """INSERT INTO initialization_attempts VALUES (?,?,?,?,?,?)
                ON CONFLICT(execution_id) DO UPDATE SET payload=excluded.payload""",
                (
                    node.execution_id,
                    run_id,
                    node.key,
                    node.generation,
                    node.ordinal,
                    node.model_dump_json(),
                ),
            )

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        run: RunRecord,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        run.state_seq += 1
        run.updated_at = utc_now()
        db.execute(
            "UPDATE initialization_runs SET payload=? WHERE id=?",
            (
                run.model_dump_json(),
                run.initialization_id,
            ),
        )
        db.execute(
            "INSERT INTO initialization_events(run_id,kind,payload,created_at) VALUES(?,?,?,?)",
            (run.initialization_id, kind, json.dumps(payload), run.updated_at.isoformat()),
        )
        # Explicit allowlist: never mirror frozen inputs, node receipts, or model text.
        summary = run.model_dump(mode="json", exclude={"error"})
        summary["has_error"] = run.error is not None
        failed = db.execute(
            "SELECT node_key FROM initialization_nodes WHERE run_id=? "
            "AND json_extract(payload,'$.status')='FAILED' ORDER BY rowid LIMIT 10",
            (run.initialization_id,),
        ).fetchall()
        summary["failed_nodes"] = [str(row[0])[:96] for row in failed]
        summary["diagnostics_count"] = db.execute(
            "SELECT COALESCE(SUM(COALESCE("
            "json_array_length(payload,'$.result.quality_annotations'),0)"
            "+CASE WHEN json_extract(payload,'$.status')='FAILED' THEN 1 ELSE 0 END),0) "
            "FROM initialization_nodes WHERE run_id=?",
            (run.initialization_id,),
        ).fetchone()[0]
        revision_row = db.execute(
            "SELECT payload FROM activation_revisions WHERE revision_id=?",
            (run.initialization_id + "-activation",),
        ).fetchone()
        summary["activation_manifest"] = {}
        if revision_row:
            revision = json.loads(revision_row[0])
            summary["activation_manifest"] = {
                "revision_id": revision["revision_id"],
                "base_revision": revision["base_revision"],
                "artifacts": {
                    role: {
                        key: (value[:160] if isinstance(value, str) else value)
                        for key, value in reference.items()
                        if key in {"run_id", "initialization_id", "version", "sha256"}
                        and isinstance(value, (str, int))
                    }
                    for role, reference in revision["artifacts"].items()
                    if role
                    in {
                        "document1",
                        "document2",
                        "document3",
                        "event_library",
                        "monitoring_configuration",
                    }
                    and isinstance(reference, dict)
                },
            }
        action = db.execute(
            "SELECT kind,payload,created_at FROM initialization_events WHERE run_id=? "
            "AND (kind LIKE 'manual.%' OR (kind='submitted' "
            "AND json_extract(payload,'$.reason') IS NOT NULL)) "
            "ORDER BY rowid DESC LIMIT 1",
            (run.initialization_id,),
        ).fetchone()
        summary["last_operator_action"] = {}
        if action:
            operation = json.loads(action[1])
            summary["last_operator_action"] = {
                "kind": action[0],
                "actor": "local_operator",
                "at": action[2],
                "reason_sha256": hashlib.sha256(
                    str(operation.get("reason", "")).encode()
                ).hexdigest(),
            }
        db.execute(
            """INSERT INTO initialization_outbox VALUES(?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET state_seq=excluded.state_seq,
            payload=excluded.payload""",
            (
                run.initialization_id,
                run.state_seq,
                json.dumps(summary),
            ),
        )

    def claim(self, owner: str, *, lease_seconds: float = 60) -> Lease | None:
        with self._write() as db:
            from doxagent.v2_control.mirror import migrate

            migrate(db)
            row = db.execute(
                """SELECT o.* FROM ticker_operations o
                JOIN initialization_runs r ON r.id=o.run_id
                LEFT JOIN v2_consumer_control c ON c.ticker=o.ticker
                WHERE o.lease_until<=? AND (c.ticker IS NULL OR
                    json_extract(c.payload,'$.initialization_allowed')=1)
                ORDER BY r.rowid LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            run = self._run(db, row["run_id"])
            if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                return None
            token = row["token"] + 1
            db.execute(
                "UPDATE ticker_operations SET owner=?,token=?,lease_until=? WHERE ticker=?",
                (owner, token, time.time() + lease_seconds, run.ticker),
            )
            run.status = RunStatus.RUNNING
            self._event(db, run, "claimed", {"token": token})
            return Lease(initialization_id=run.initialization_id, owner=owner, token=token)

    @staticmethod
    def _fence(db: sqlite3.Connection, lease: Lease) -> None:
        row = db.execute(
            "SELECT * FROM ticker_operations WHERE run_id=?", (lease.initialization_id,)
        ).fetchone()
        if (
            row is None
            or row["owner"] != lease.owner
            or row["token"] != lease.token
            or (row["lease_until"] <= time.time())
        ):
            raise LeaseLost("initialization lease expired or superseded")

    def heartbeat(self, lease: Lease, *, lease_seconds: float = 60) -> None:
        with self._write() as db:
            self._fence(db, lease)
            db.execute(
                "UPDATE ticker_operations SET lease_until=? WHERE run_id=?",
                (
                    time.time() + lease_seconds,
                    lease.initialization_id,
                ),
            )

    def assert_lease(self, lease: Lease) -> None:
        """Read-only fencing check for owned local execution processes."""
        with self._connection() as db:
            self._fence(db, lease)

    def begin(
        self,
        lease: Lease,
        key: str,
        execution_version: dict[str, str],
    ) -> NodeRecord:
        with self._write() as db:
            self._fence(db, lease)
            from doxagent.v2_control.mirror import permit

            run = self._run(db, lease.initialization_id)
            permit(db, run.ticker, epoch=run.control_epoch, initialization=True)
            node = self._node(db, lease.initialization_id, key)
            if node.status in {"SUCCEEDED", "RUNNING"}:
                return node
            if node.ordinal >= 2:
                raise BudgetExhausted(key)
            for dependency in node.dependencies:
                if self._node(db, lease.initialization_id, dependency).status != "SUCCEEDED":
                    raise InitializationError(f"dependency not complete: {dependency}")
            node.ordinal += 1
            node.execution_id = uuid4().hex
            node.execution_version = execution_version
            node.status = "RUNNING"
            node.error = None
            node.receipt = {}
            frozen_dependencies = {}
            for dependency in node.dependencies:
                result = self._node(db, lease.initialization_id, dependency).result
                if result is not None:
                    frozen_dependencies[dependency] = result.model_dump(mode="json")
            node.receipt["dependency_results"] = frozen_dependencies
            self._save_node(db, lease.initialization_id, node)
            run = self._run(db, lease.initialization_id)
            run.phase = progress_phase(node)
            self._event(db, run, "node.started", {"node": key, "execution": node.execution_id})
            return node

    def receipt(self, lease: Lease, key: str, values: dict[str, Any]) -> None:
        with self._write() as db:
            self._fence(db, lease)
            node = self._node(db, lease.initialization_id, key)
            if node.status != "RUNNING":
                raise InitializationError("receipt requires running node")
            node.receipt.update(values)
            self._save_node(db, lease.initialization_id, node)

    def complete(self, lease: Lease, key: str, result: NodeResult) -> None:
        self._settle(lease, key, result=result)

    def reconcile_native_success(self, lease: Lease, key: str) -> None:
        """Recognize a committed native task, including a usable degraded result."""
        with self._write() as db:
            self._fence(db, lease)
            node = self._node(db, lease.initialization_id, key)
            if node.inputs.get("kind") != "cdecr_native":
                raise InitializationError("native reconciliation requires a native task")
            if node.status == "SUCCEEDED":
                return
            node.status = "SUCCEEDED"
            node.error = None
            node.result = NodeResult(artifacts={"native_committed": True})
            self._save_node(db, lease.initialization_id, node)
            self._event(
                db, self._run(db, lease.initialization_id), "native.reconciled", {"node": key}
            )

    def fail(self, lease: Lease, key: str, error: str) -> None:
        self._settle(lease, key, error=error)

    def _settle(
        self,
        lease: Lease,
        key: str,
        *,
        result: NodeResult | None = None,
        error: str | None = None,
    ) -> None:
        with self._write() as db:
            self._fence(db, lease)
            node = self._node(db, lease.initialization_id, key)
            if node.status != "RUNNING":
                raise InitializationError("node settlement requires RUNNING")
            node.status = "SUCCEEDED" if result is not None else "FAILED"
            node.result = result
            node.error = error
            self._save_node(db, lease.initialization_id, node)
            self._event(
                db,
                self._run(db, lease.initialization_id),
                "node.settled",
                {
                    "node": key,
                    "status": node.status,
                },
            )

    def finish(self, lease: Lease, *, error: str | None = None) -> RunRecord:
        with self._write() as db:
            self._fence(db, lease)
            nodes = [
                NodeRecord.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_nodes WHERE run_id=?",
                    (lease.initialization_id,),
                )
            ]
            if any(n.status == "RUNNING" and not n.inputs.get("managed_by") for n in nodes):
                raise InitializationError("cannot release ticker with running nodes")
            for node in nodes:
                if node.status == "RUNNING":
                    # A failed parent can leave a locally interrupted child. Preserve
                    # its receipt for reattachment but never release a RUNNING ledger.
                    if error is None:
                        raise InitializationError("unsettled internal execution")
                    node.status = "FAILED"
                    node.error = "parent stopped before internal stage settlement"
                    self._save_node(db, lease.initialization_id, node)
            if error is None and any(
                n.status != "SUCCEEDED" and not n.inputs.get("managed_by") for n in nodes
            ):
                raise InitializationError("unfinished workflow cannot succeed")
            run = self._run(db, lease.initialization_id)
            run.status = RunStatus.FAILED if error else RunStatus.SUCCEEDED
            if error is None and any(n.key == "runtime.ready" for n in nodes):
                run.phase = "VERIFY_READY"
            run.error = error
            run.manual_resume_required = bool(error)
            self._event(db, run, "finished", {"status": run.status})
            db.execute("DELETE FROM ticker_operations WHERE run_id=?", (lease.initialization_id,))
            return run

    def resume(
        self,
        run_id: str,
        *,
        reason: str,
        node_key: str | None = None,
        control_epoch: int | None = None,
        control_operation_id: str | None = None,
    ) -> RunRecord:
        if not reason.strip():
            raise ValueError("operator reason required")
        with self._write() as db:
            run = self._run(db, run_id)
            if control_operation_id and run.control_operation_id == control_operation_id:
                return run
            if run.error == "OPERATOR_STOPPED":
                raise InitializationError("removed initialization cannot be resumed")
            if run.status != RunStatus.FAILED:
                raise InitializationError("resume requires FAILED initialization")
            if db.execute(
                "SELECT 1 FROM ticker_operations WHERE ticker=?", (run.ticker,)
            ).fetchone():
                raise InitializationError("DUPLICATE_ACTIVE_INITIALIZATION")
            nodes = [
                NodeRecord.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_nodes WHERE run_id=?", (run_id,)
                )
            ]
            targets = [
                n for n in nodes if n.status == "FAILED" and (node_key is None or n.key == node_key)
            ]
            if not targets:
                raise InitializationError("no selected failed node")
            if any(n.inputs.get("_invalidated") for n in targets):
                raise InitializationError("invalidated handoffs require adopt or isolated rerun")
            parents = {n.inputs.get("managed_by") for n in targets}
            targets.extend(n for n in nodes if n.key in parents and n not in targets)
            commit = next((n for n in nodes if n.key == "activation.commit"), None)
            active = db.execute(
                "SELECT revision_id FROM ticker_active_revision WHERE ticker=?", (run.ticker,)
            ).fetchone()
            if (
                commit
                and commit.status == "SUCCEEDED"
                and (active is None or active[0] != run_id + "-activation")
            ):
                targets.extend(
                    n
                    for n in nodes
                    if n.key in {"activation.commit", "bus.ready", "runtime.ready"}
                    and n not in targets
                )
            for node in targets:
                node.generation += 1
                node.ordinal = 0
                node.execution_id = None
                node.status = "PENDING"
                node.error = None
                # Previous checkpoint remains discoverable in immutable attempt history.
                self._save_node(db, run_id, node)
            run.status = RunStatus.QUEUED
            if control_operation_id:
                from doxagent.v2_control.mirror import permit

                permit(db, run.ticker, epoch=control_epoch, initialization=True)
                run.control_epoch = control_epoch
                run.control_operation_id = control_operation_id
            run.manual_resume_required = False
            run.error = None
            db.execute(
                "INSERT INTO ticker_operations(ticker,run_id) VALUES (?,?)", (run.ticker, run_id)
            )
            self._event(
                db, run, "manual.resume", {"reason": reason, "nodes": [n.key for n in targets]}
            )
            return run

    def attempts(self, run_id: str, key: str) -> list[NodeRecord]:
        with self._connection() as db:
            return [
                NodeRecord.model_validate_json(row[0])
                for row in db.execute(
                    """SELECT payload FROM initialization_attempts WHERE run_id=? AND node_key=?
                ORDER BY generation,ordinal""",
                    (run_id, key),
                )
            ]

    def adopt(self, run_id: str, key: str, result: NodeResult, *, reason: str) -> RunRecord:
        """Replace an inactive initialization result and continue only unfinished work."""
        if not reason.strip():
            raise ValueError("operator reason required")
        with self._write() as db:
            run = self._run(db, run_id)
            if db.execute(
                "SELECT 1 FROM ticker_operations WHERE ticker=?", (run.ticker,)
            ).fetchone():
                raise InitializationError("DUPLICATE_ACTIVE_INITIALIZATION")
            node = self._node(db, run_id, key)
            if node.inputs.get("managed_by"):
                raise ValueError(
                    "adopt requires a published block handoff, not an internal turn result"
                )
            node.generation += 1
            node.ordinal = 1
            node.execution_id = uuid4().hex
            node.status = "SUCCEEDED"
            node.result = result
            node.error = None
            node.receipt = {"operator_reason": reason, "adopted": True}
            node.inputs.pop("_invalidated", None)
            self._save_node(db, run_id, node)
            # A previously completed ticker keeps serving its active revision.
            # Adoption does not implicitly create an activation or clear successors.
            if run.status == RunStatus.FAILED:
                run.status = RunStatus.QUEUED
                run.manual_resume_required = False
                run.error = None
                db.execute(
                    "INSERT INTO ticker_operations(ticker,run_id) VALUES(?,?)", (run.ticker, run_id)
                )
            self._event(db, run, "manual.adopt", {"node": key, "reason": reason})
            return run

    def invalidate(self, run_id: str, keys: list[str], *, reason: str) -> RunRecord:
        """Invalidate exactly the operator-selected handoffs without executing any node."""
        if not keys or not reason.strip() or len(set(keys)) != len(keys):
            raise ValueError("unique explicit node keys and operator reason required")
        with self._write() as db:
            run = self._run(db, run_id)
            if db.execute(
                "SELECT 1 FROM ticker_operations WHERE ticker=?", (run.ticker,)
            ).fetchone():
                raise InitializationError("DUPLICATE_ACTIVE_INITIALIZATION")
            nodes = [self._node(db, run_id, key) for key in keys]
            for node in nodes:
                node.generation += 1
                node.ordinal = 0
                node.execution_id = None
                node.status = "FAILED"
                node.error = "operator invalidated this handoff; adopt or isolated rerun required"
                node.result = None
                node.inputs["_invalidated"] = True
                self._save_node(db, run_id, node)
            run.status = RunStatus.FAILED
            run.manual_resume_required = True
            self._event(db, run, "manual.invalidate", {"nodes": keys, "reason": reason})
            return run

    def rerun(
        self,
        run_id: str,
        *,
        reason: str,
        node_key: str | None = None,
        block: str | None = None,
    ) -> RunRecord:
        """Create an isolated selected-node plan; successors are never included implicitly."""
        if not reason.strip() or (node_key is None) == (block is None):
            raise ValueError("reason and exactly one node/block selector required")
        source = self.get(run_id)
        originals = self.nodes(run_id)
        selected = [n for n in originals if n.key == node_key or (block and n.block == block)]
        if not selected:
            raise ValueError("no matching nodes")
        if node_key and selected[0].inputs.get("managed_by"):
            receipts = [
                n.receipt
                for n in self.attempts(run_id, node_key)
                if n.receipt.get("invocation") and n.receipt.get("snapshot")
            ]
            if not receipts:
                raise InitializationError("selected node predates durable invocation snapshots")
            return self.submit(
                source.ticker,
                source.research_cutoff_at,
                [
                    NodeSpec(
                        key="internal.rerun",
                        block=selected[0].block,
                        inputs={
                            "_selected_invocation": receipts[0]["invocation"],
                            "_snapshot": receipts[0]["snapshot"],
                            "_source_initialization": run_id,
                            "_source_node": node_key,
                            "_operator_reason": reason,
                        },
                    )
                ],
                reinitialize=True,
                operation_kind="RERUN_NODE",
            )
        selected = [n for n in selected if not n.inputs.get("managed_by")]
        if not selected:
            raise InitializationError("selected block has no registered workflow container")
        selected_keys = {n.key for n in selected}
        plan = []
        for node in selected:
            # Freeze dependencies outside the selected scope in this new execution.
            external: dict[str, Any] = {}
            for dependency in node.dependencies:
                if dependency in selected_keys:
                    continue
                upstream = next(n for n in originals if n.key == dependency)
                if upstream.result is None:
                    raise InitializationError(f"missing completed dependency: {dependency}")
                external[dependency] = upstream.result.model_dump(mode="json")
            plan.append(
                NodeSpec(
                    key=node.key,
                    block=node.block,
                    dependencies=[key for key in node.dependencies if key in selected_keys],
                    inputs={
                        **node.inputs,
                        "_replay_dependencies": external,
                        "_source_initialization": run_id,
                        "_operator_reason": reason,
                    },
                )
            )
        return self.submit(
            source.ticker,
            source.research_cutoff_at,
            plan,
            reinitialize=True,
            operation_kind="RERUN_NODE" if node_key else "RERUN_BLOCK",
        )

    def expand(self, lease: Lease, plan: list[NodeSpec]) -> None:
        """Register discovered wave/shell nodes once, preserving any completed work."""
        with self._write() as db:
            self._fence(db, lease)
            existing = [
                NodeRecord.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_nodes WHERE run_id=?",
                    (lease.initialization_id,),
                )
            ]
            by_key = {n.key: n for n in existing}
            for spec in plan:
                old = by_key.get(spec.key)
                if old is not None:
                    old_spec = NodeSpec(
                        **old.model_dump(
                            include={
                                "key",
                                "block",
                                "dependencies",
                                "inputs",
                            }
                        )
                    )
                    if old_spec != spec:
                        raise InitializationError(f"frozen node plan conflict: {spec.key}")
                else:
                    by_key[spec.key] = NodeRecord(**spec.model_dump())
            self._validate_plan(
                [
                    NodeSpec(
                        **n.model_dump(
                            include={
                                "key",
                                "block",
                                "dependencies",
                                "inputs",
                            }
                        )
                    )
                    for n in by_key.values()
                ]
            )
            for node in by_key.values():
                self._save_node(db, lease.initialization_id, node)
            self._event(
                db,
                self._run(db, lease.initialization_id),
                "plan.expanded",
                {
                    "keys": [n.key for n in plan],
                },
            )

    def outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM initialization_outbox ORDER BY rowid LIMIT ?", (limit,)
                )
            ]

    def acknowledge_summary(self, run_id: str, state_seq: int) -> None:
        with self._write() as db:
            db.execute(
                "DELETE FROM initialization_outbox WHERE run_id=? AND state_seq=?",
                (run_id, state_seq),
            )

    def active_revision(self, ticker: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """SELECT r.payload FROM activation_revisions r
                JOIN ticker_active_revision a ON a.revision_id=r.revision_id
                WHERE a.ticker=?""",
                (ticker.upper(),),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def active_revisions(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT r.payload FROM activation_revisions r JOIN ticker_active_revision a "
                    "ON a.revision_id=r.revision_id ORDER BY a.ticker"
                )
            ]

    def revision(self, revision_id: str) -> dict[str, Any]:
        with self._connection() as db:
            row = db.execute(
                "SELECT payload FROM activation_revisions WHERE revision_id=?", (revision_id,)
            ).fetchone()
            if row is None:
                raise KeyError(revision_id)
            value: dict[str, Any] = json.loads(row[0])
            return value

    def acknowledge_revision(self, ticker: str, revision_id: str, worker: str) -> bool:
        if worker not in {"bus", "runtime"}:
            raise ValueError("unknown activation consumer")
        with self._write() as db:
            row = db.execute(
                "SELECT revision_id FROM ticker_active_revision WHERE ticker=?", (ticker.upper(),)
            ).fetchone()
            if row is None or row[0] != revision_id:
                return False
            db.execute(
                "INSERT INTO activation_worker_ack VALUES(?,?,?,?) "
                "ON CONFLICT(ticker,worker) DO UPDATE SET revision_id=excluded.revision_id, "
                "heartbeat=excluded.heartbeat",
                (ticker.upper(), worker, revision_id, time.time()),
            )
            return True

    def revision_acknowledged(
        self,
        ticker: str,
        revision_id: str,
        worker: str,
        *,
        max_age: float = 60,
    ) -> bool:
        with self._connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM activation_worker_ack WHERE ticker=? AND worker=? "
                    "AND revision_id=? AND heartbeat>=?",
                    (ticker.upper(), worker, revision_id, time.time() - max_age),
                ).fetchone()
                is not None
            )

    def rollback_revision(self, lease: Lease, revision_id: str) -> bool:
        with self._write() as db:
            self._fence(db, lease)
            run = self._run(db, lease.initialization_id)
            row = db.execute(
                "SELECT base_revision FROM activation_revisions WHERE revision_id=? AND ticker=?",
                (revision_id, run.ticker),
            ).fetchone()
            if row is None or row[0] is None:
                return False  # First initialization remains failed/not-ready for exact resume.
            current = db.execute(
                "SELECT revision_id FROM ticker_active_revision WHERE ticker=?", (run.ticker,)
            ).fetchone()
            if current and current[0] == row[0]:
                return True
            if current is None or current[0] != revision_id:
                return False
            db.execute(
                "UPDATE ticker_active_revision SET revision_id=? WHERE ticker=?",
                (row[0], run.ticker),
            )
            self._event(db, run, "revision.rolled_back", {"revision_id": revision_id})
            return True

    def stage_revision(
        self,
        lease: Lease,
        revision_id: str,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        with self._write() as db:
            self._fence(db, lease)
            run = self._run(db, lease.initialization_id)
            payload = {
                "revision_id": revision_id,
                "ticker": run.ticker,
                "base_revision": run.base_revision,
                "research_cutoff_at": run.research_cutoff_at.isoformat(),
                "artifacts": artifacts,
            }
            encoded = json.dumps(payload, sort_keys=True)
            old = db.execute(
                "SELECT payload FROM activation_revisions WHERE revision_id=?", (revision_id,)
            ).fetchone()
            if old and old[0] != encoded:
                raise InitializationError("immutable revision conflict")
            db.execute(
                "INSERT OR IGNORE INTO activation_revisions VALUES(?,?,?,?)",
                (
                    revision_id,
                    run.ticker,
                    run.base_revision,
                    encoded,
                ),
            )
            return payload

    def activate_runtime_bundle(
        self,
        *,
        ticker: str,
        identity: str,
        base: dict[str, Any],
        event_ref: dict[str, Any],
        policy_ref: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Short dependency CAS. Runtime never acquires an initialization-wide lease."""
        with self._write() as db:
            from doxagent.v2_control.mirror import permit

            permit(db, ticker, epoch=metadata.get("control_epoch"))
            prior = db.execute(
                "SELECT payload FROM activation_revisions WHERE revision_id=?", (identity,)
            ).fetchone()
            if prior:
                return json.loads(prior[0])
            row = db.execute(
                "SELECT a.payload FROM ticker_active_revision t "
                "JOIN activation_revisions a ON a.revision_id=t.revision_id "
                "WHERE t.ticker=?",
                (ticker,),
            ).fetchone()
            if row is None:
                raise InitializationError("runtime activation requires initialized ticker")
            current = json.loads(row[0])
            for role in ("document1", "document2", "event_library", "document3"):
                if current["artifacts"][role] != base["artifacts"][role]:
                    raise InitializationError("runtime maintenance superseded by activation")
            value = {
                **current,
                "revision_id": identity,
                "base_revision": current["revision_id"],
                "artifacts": {
                    **current["artifacts"],
                    "event_library": event_ref,
                    "document3": policy_ref,
                },
                "runtime_metadata": metadata,
            }
            db.execute(
                "INSERT INTO activation_revisions VALUES(?,?,?,?)",
                (identity, ticker, current["revision_id"], json.dumps(value, sort_keys=True)),
            )
            db.execute(
                "UPDATE ticker_active_revision SET revision_id=? WHERE ticker=?", (identity, ticker)
            )
            return value

    def activate(self, lease: Lease, revision_id: str) -> None:
        with self._write() as db:
            self._fence(db, lease)
            run = self._run(db, lease.initialization_id)
            from doxagent.v2_control.mirror import permit

            permit(db, run.ticker, epoch=run.control_epoch, initialization=True)
            revision = db.execute(
                "SELECT * FROM activation_revisions WHERE revision_id=?", (revision_id,)
            ).fetchone()
            if revision is None or revision["ticker"] != run.ticker:
                raise InitializationError("unknown ticker revision")
            current = db.execute(
                "SELECT revision_id FROM ticker_active_revision WHERE ticker=?", (run.ticker,)
            ).fetchone()
            current_id = current[0] if current else None
            if current_id == revision_id:
                return
            if current_id != revision["base_revision"] and run.operation_kind not in {
                "ACTIVATE",
                "REPLACE_ARTIFACT",
                "ROLLBACK",
            }:
                raise InitializationError("active revision changed")
            db.execute(
                """INSERT INTO ticker_active_revision VALUES(?,?)
                ON CONFLICT(ticker) DO UPDATE SET revision_id=excluded.revision_id""",
                (run.ticker, revision_id),
            )
            self._event(db, run, "revision.activated", {"revision_id": revision_id})
