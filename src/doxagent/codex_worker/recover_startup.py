"""Audited offline recovery of a job that never reached a model thread.

Uses the same writer lock as the Worker; requests, identities and workspace
inputs are retained. This is not a retry mechanism for uncertain business turns.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.schema import WorkerEvent, WorkerJob, utc_now
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.trade_execution.worker import WriterLock


def recover_startup(store: JobStore, job_id: str, *, reason: str) -> WorkerJob:
    if not reason.strip():
        raise ValueError("startup recovery reason required")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload,request FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("unknown job")
        job = WorkerJob.model_validate_json(row[0])
        if (
            job.status != "failed"
            or job.error_code != "WORKER_INFRA_RECOVERY_EXHAUSTED"
            or job.thread_id
            or job.turn_id
            or job.execution_phase != "SETTLED"
            or not (job.error_message or "").startswith("START_THREAD:")
            or not row[1]
        ):
            raise ValueError("only a settled, pre-thread infrastructure failure can be recovered")
        before = job.model_dump(mode="json")
        history = list(job.resource_receipt.get("manual_startup_recoveries", []))
        history.append(
            {
                "at": utc_now().isoformat(),
                "reason": reason.strip(),
                "error_code": job.error_code,
                "error_message": job.error_message,
                "infra_recovery_count": job.infra_recovery_count,
            }
        )
        job = job.model_copy(
            update={
                "status": "queued",
                "execution_phase": "QUEUED",
                "infra_recovery_count": 0,
                "error_code": None,
                "error_message": None,
                "next_retry_at": 0,
                "wait_reason": None,
                "started_at": None,
                "finished_at": None,
                "updated_at": utc_now(),
                "resource_receipt": {**job.resource_receipt, "manual_startup_recoveries": history},
            }
        )
        db.execute(
            "UPDATE jobs SET status=?,payload=? WHERE id=?",
            (job.status, job.model_dump_json(), job_id),
        )
        sequence = db.execute(
            "SELECT COALESCE(MAX(sequence),-1)+1 FROM events WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        event = WorkerEvent(
            sequence=sequence,
            event_type="job.manual_startup_recovery",
            payload={"reason": reason.strip(), "before": before, "preserved_request": True},
        )
        db.execute("INSERT INTO events VALUES(?,?,?)", (job_id, sequence, event.model_dump_json()))
    return job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    with WriterLock(args.root / "codex-worker"):
        store = JobStore(args.root)
        job = recover_startup(store, args.job_id, reason=args.reason)
        LocalWorkspaceStore(args.root).write_text(
            job.run_id, f"audit/jobs/{job.job_id}.json", job.model_dump_json()
        )
        print(job.model_dump_json(exclude={"telemetry", "final_response"}))


if __name__ == "__main__":
    main()
