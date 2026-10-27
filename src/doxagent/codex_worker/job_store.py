"""Durable queue/index; historical payloads and events are read on demand."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .schema import WorkerEvent, WorkerJob, WorkerRunRequest


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "worker-jobs.sqlite3"
        with closing(self.connect()) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, request TEXT,
                    priority INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, sequence);
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(job_id, sequence));
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            """)
            if db.execute("SELECT 1 FROM metadata WHERE key='legacy_imported'").fetchone() is None:
                # One record at a time, never retain all historical telemetry in RAM.
                for path in root.glob("*/audit/jobs/*.json"):
                    try:
                        job = WorkerJob.model_validate_json(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO jobs(id,status,payload) VALUES(?,?,?)",
                        (job.job_id, job.status, job.model_dump_json()),
                    )
                db.execute("INSERT INTO metadata VALUES('legacy_imported','1')")

    def connect(self) -> sqlite3.Connection:
        from doxagent.control_io import SHORT_WRITE

        db = sqlite3.connect(self.path, timeout=0.1 if SHORT_WRITE.get() else 30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def get(self, job_id: str) -> WorkerJob | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return WorkerJob.model_validate_json(row[0]) if row else None

    def save(self, job: WorkerJob, request: WorkerRunRequest | None = None) -> None:
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if (
                request is not None
                and not db.execute("SELECT 1 FROM jobs WHERE id=?", (job.job_id,)).fetchone()
            ):
                if db.execute(
                    "SELECT 1 FROM metadata WHERE key=?",
                    ("workspace_retirement:" + request.run_id,),
                ).fetchone():
                    raise ValueError("PUBLISHED_WORKSPACE_INPUTS_RETIRED_CREATE_NEW_RUN")
            db.execute(
                """INSERT INTO jobs(id,status,payload,request,priority) VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload,
                request=COALESCE(excluded.request,jobs.request)""",
                (
                    job.job_id,
                    job.status,
                    job.model_dump_json(),
                    request.model_dump_json() if request else None,
                    int(
                        request is not None and request.research_lane.value == "persistent_runtime"
                    ),
                ),
            )

    def request(self, job_id: str) -> WorkerRunRequest | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT request FROM jobs WHERE id=?", (job_id,)).fetchone()
        return WorkerRunRequest.model_validate_json(row[0]) if row and row[0] else None

    def active(self) -> list[str]:
        with closing(self.connect()) as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT id FROM jobs WHERE status IN ('queued','running') ORDER BY sequence"
                )
            ]

    def acknowledge(self, job_id: str) -> WorkerJob:
        job = self.get(job_id)
        request = self.request(job_id)
        if job is None or job.status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("only a terminal persisted job may be acknowledged")
        if request is not None and request.task_phase not in {
            "w3_template",
            "trade_decision",
            "post_decision_operations",
        }:
            raise ValueError("payload compaction is restricted to W3 Case phases")
        if job.telemetry:
            job.telemetry.events = []
        job.final_response = None
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE jobs SET request=NULL,payload=? WHERE id=?", (job.model_dump_json(), job_id)
            )
            db.execute("DELETE FROM events WHERE job_id=?", (job_id,))
        if request is not None:
            from .result_receipt import receipt_path

            run_root = (self.root / request.run_id).resolve()
            targets = [
                run_root / "attempts" / request.attempt_id / "audit" / "agent_loop.jsonl",
                receipt_path(self.root, request),
            ]
            for target in targets:
                if not target.resolve().is_relative_to(self.root.resolve()):
                    raise ValueError("job payload path escapes worker root")
                target.unlink(missing_ok=True)
            audit = run_root / "audit" / "jobs" / (job_id + ".json")
            if audit.is_file() and audit.resolve().is_relative_to(self.root.resolve()):
                audit.write_text(job.model_dump_json(), encoding="utf-8")
        return job

    @staticmethod
    def _lane_expression() -> str:
        return (
            "CASE WHEN json_extract(request,'$.execution_lane') IN ('realtime','background') "
            "THEN json_extract(request,'$.execution_lane') "
            "WHEN json_extract(request,'$.research_lane')='persistent_runtime' "
            "AND json_extract(request,'$.run_id') NOT LIKE 'runtime-maintain-%' "
            "THEN 'realtime' ELSE 'background' END"
        )

    def queued(
        self,
        *,
        lane: str | None = None,
        prefer_runtime: bool | None = None,
    ) -> list[str]:
        del prefer_runtime  # Compatibility only; lane policy owns ordering now.
        lane_expression = self._lane_expression()
        where = "status='queued'"
        values: tuple[object, ...] = ()
        if lane is not None:
            where += f" AND {lane_expression}=?"
            values = (lane,)
        order = (
            "CASE WHEN json_extract(request,'$.deadline_at') IS NULL THEN 1 ELSE 0 END,"
            "json_extract(request,'$.deadline_at'),sequence"
        )
        with closing(self.connect()) as db:
            return [
                r[0]
                for r in db.execute(f"SELECT id FROM jobs WHERE {where} ORDER BY {order}", values)
            ]

    def queued_counts(self) -> dict[str, int]:
        expression = self._lane_expression()
        with closing(self.connect()) as db:
            rows = db.execute(
                f"SELECT {expression},COUNT(*) FROM jobs WHERE status='queued' GROUP BY 1"
            ).fetchall()
        return {str(lane): int(count) for lane, count in rows}

    def oldest_queued(self, lane: str) -> WorkerJob | None:
        expression = self._lane_expression()
        with closing(self.connect()) as db:
            row = db.execute(
                f"SELECT payload FROM jobs WHERE status='queued' AND {expression}=? "
                "ORDER BY sequence LIMIT 1",
                (lane,),
            ).fetchone()
        return WorkerJob.model_validate_json(row[0]) if row else None

    def event(self, job_id: str, kind: str, payload: dict[str, Any]) -> None:
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            seq = db.execute(
                "SELECT COALESCE(MAX(sequence),-1)+1 FROM events WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            event = WorkerEvent(sequence=seq, event_type=kind, payload=payload)
            db.execute("INSERT INTO events VALUES(?,?,?)", (job_id, seq, event.model_dump_json()))

    def events(self, job_id: str, after: int) -> list[WorkerEvent]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT payload FROM events WHERE job_id=? AND sequence>? "
                "ORDER BY sequence LIMIT 128",
                (job_id, after),
            ).fetchall()
        return [WorkerEvent.model_validate_json(row[0]) for row in rows]

    def metadata(self, key: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
        with closing(self.connect()) as db, db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, json.dumps(value)))
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}
