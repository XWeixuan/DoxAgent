"""In-process execution supervisor with durable job snapshots and event streams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

from doxagent.codex_runtime.schema import utc_now
from doxagent.codex_worker.schema import (
    WorkerEvent,
    WorkerJob,
    WorkerRunRequest,
    WorkerTurnTelemetry,
)
from doxagent.codex_worker.sdk_runtime import CodexExecutionRuntime, TurnHandle
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore


class WorkerJobManager:
    def __init__(self, runtime: CodexExecutionRuntime, workspaces: LocalWorkspaceStore) -> None:
        self._runtime = runtime
        self._workspaces = workspaces
        self._jobs: dict[str, WorkerJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._handles: dict[str, TurnHandle] = {}
        self._events: dict[str, list[WorkerEvent]] = defaultdict(list)
        self._condition = asyncio.Condition()
        self._recover_snapshots()

    async def submit(self, request: WorkerRunRequest) -> WorkerJob:
        request_sha256 = hashlib.sha256(
            json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        job_id = (
            hashlib.sha256(f"{request.run_id}:{request.idempotency_key}".encode()).hexdigest()
            if request.idempotency_key
            else uuid4().hex
        )
        existing = self._jobs.get(job_id)
        if existing is not None:
            if existing.request_sha256 != request_sha256:
                raise ValueError("idempotency key reused for a different worker request")
            return existing.model_copy(deep=True)
        job = WorkerJob(
            job_id=job_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            request_sha256=request_sha256,
            status="queued",
        )
        # Persist before the first await: duplicate HTTP submissions cannot dispatch twice.
        self._persist(job)
        self._jobs[job.job_id] = job
        await self._emit(job.job_id, "job.queued", {"node": request.node.value})
        self._tasks[job.job_id] = asyncio.create_task(self._run(job.job_id, request))
        return job.model_copy(deep=True)

    def get(self, job_id: str) -> WorkerJob | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    async def cancel(self, job_id: str) -> WorkerJob | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job.model_copy(deep=True)
        handle = self._handles.get(job_id)
        if handle is not None:
            with suppress(Exception):
                await handle.interrupt()
        task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()
        await self._finish(job_id, status="cancelled")
        return self._jobs[job_id].model_copy(deep=True)

    async def events(self, job_id: str, after_sequence: int = -1) -> AsyncIterator[WorkerEvent]:
        while True:
            if job_id not in self._jobs:
                return
            pending = [event for event in self._events[job_id] if event.sequence > after_sequence]
            for event in pending:
                after_sequence = event.sequence
                yield event
            if self._jobs[job_id].status in {"succeeded", "failed", "cancelled"}:
                return
            async with self._condition:
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=15)
                except TimeoutError:
                    yield WorkerEvent(
                        sequence=after_sequence,
                        event_type="heartbeat",
                        payload={},
                    )

    async def _run(self, job_id: str, request: WorkerRunRequest) -> None:
        job = self._jobs[job_id]
        started = time.monotonic()
        try:
            attempt_root = self._workspaces.ensure_attempt(request.run_id, request.attempt_id)
            run_root = attempt_root.parents[1]
            await self._update(job_id, status="running", started_at=utc_now())
            handle = await self._runtime.start(request, run_root)
            self._handles[job_id] = handle
            result = await asyncio.wait_for(handle.run(), timeout=request.timeout_seconds)
            telemetry = result.telemetry
            if telemetry is not None:
                telemetry = telemetry.model_copy(
                    update={"worker_wall_time_ms": int((time.monotonic() - started) * 1000)}
                )
            if result.status.lower() not in {"completed", "succeeded", "success"}:
                await self._finish(
                    job_id,
                    status="failed",
                    thread_id=result.thread_id,
                    turn_id=result.turn_id,
                    final_response=result.final_response,
                    error_code="CODEX_TURN_FAILED",
                    error_message=result.error_message or result.status,
                    telemetry=telemetry,
                )
                self._persist_telemetry(request, self._jobs[job_id])
                return
            await self._finish(
                job_id,
                status="succeeded",
                thread_id=result.thread_id,
                turn_id=result.turn_id,
                final_response=result.final_response,
                telemetry=telemetry,
            )
            self._persist_telemetry(request, self._jobs[job_id])
        except asyncio.CancelledError:
            if job.status != "cancelled":
                await self._finish(job_id, status="cancelled")
            raise
        except TimeoutError:
            active_handle = self._handles.get(job_id)
            if active_handle is not None:
                with suppress(Exception):
                    await active_handle.interrupt()
            await self._finish(
                job_id,
                status="failed",
                error_code="CODEX_TURN_TIMEOUT",
                error_message=f"turn exceeded {request.timeout_seconds} seconds",
                telemetry=self._failure_telemetry(
                    started,
                    f"timeout:{request.timeout_seconds}s",
                ),
            )
            self._persist_telemetry(request, self._jobs[job_id])
        except Exception as exc:
            await self._finish(
                job_id,
                status="failed",
                error_code=getattr(exc, "code", "CODEX_WORKER_ERROR"),
                error_message=str(exc),
                telemetry=self._failure_telemetry(started, str(exc)),
            )
            self._persist_telemetry(request, self._jobs[job_id])
        finally:
            self._handles.pop(job_id, None)

    async def _update(self, job_id: str, **updates: object) -> None:
        job = self._jobs[job_id].model_copy(update={**updates, "updated_at": utc_now()})
        self._jobs[job_id] = job
        self._persist(job)
        await self._emit(job_id, f"job.{job.status}", job.model_dump(mode="json"))

    async def _finish(self, job_id: str, **updates: object) -> None:
        await self._update(job_id, finished_at=utc_now(), **updates)

    async def _emit(self, job_id: str, event_type: str, payload: dict[str, object]) -> None:
        event = WorkerEvent(
            sequence=len(self._events[job_id]),
            event_type=event_type,
            payload=payload,
        )
        self._events[job_id].append(event)
        async with self._condition:
            self._condition.notify_all()

    def _persist(self, job: WorkerJob) -> None:
        relative = f"audit/jobs/{job.job_id}.json"
        self._workspaces.write_text(
            job.run_id,
            relative,
            json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    def _persist_telemetry(self, request: WorkerRunRequest, job: WorkerJob) -> None:
        telemetry = job.telemetry
        if telemetry is None:
            return
        base = f"attempts/{request.attempt_id}/audit"
        loop = "".join(
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            + "\n"
            for item in telemetry.events
        )
        self._workspaces.write_text(request.run_id, f"{base}/agent_loop.jsonl", loop)
        summary = {
            "schema_version": "codex-d1-turn-summary-v1",
            "run_id": request.run_id,
            "attempt_id": request.attempt_id,
            "node": request.node.value,
            "model": request.model,
            "model_provider": request.model_provider,
            "job_status": job.status,
            "thread_id": job.thread_id,
            "turn_id": job.turn_id,
            **telemetry.model_dump(mode="json", exclude={"events"}),
            "validation": {
                "schema": "pending",
                "progressive": "pending",
                "citation": "pending",
            },
        }
        self._workspaces.write_text(
            request.run_id,
            f"{base}/turn_summary.json",
            json.dumps(summary, ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _failure_telemetry(started: float, failure: str) -> WorkerTurnTelemetry:
        return WorkerTurnTelemetry(
            worker_wall_time_ms=int((time.monotonic() - started) * 1000),
            failures=[" ".join(failure.split())[:500]],
        )

    def _recover_snapshots(self) -> None:
        """Recover terminal jobs and close orphaned active jobs after restart."""

        for path in self._workspaces.root.glob("*/audit/jobs/*.json"):
            try:
                job = WorkerJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.status in {"queued", "running"}:
                job = job.model_copy(
                    update={
                        "status": "failed",
                        "error_code": "WORKER_RESTARTED",
                        "error_message": "worker restarted before the Codex turn completed",
                        "updated_at": utc_now(),
                    }
                )
                self._persist(job)
            self._jobs[job.job_id] = job
            self._events[job.job_id].append(
                WorkerEvent(
                    sequence=0,
                    event_type="job.recovered",
                    payload={"status": job.status},
                )
            )
