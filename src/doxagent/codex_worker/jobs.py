"""In-process execution supervisor with durable job snapshots and event streams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import asdict
from functools import partial
from logging.handlers import RotatingFileHandler
from uuid import uuid4

from doxagent.codex_runtime.schema import utc_now
from doxagent.codex_worker.io_budget import blocking
from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.pressure import PressureController, sample
from doxagent.codex_worker.result_receipt import receipt_path
from doxagent.codex_worker.schema import (
    WorkerEvent,
    WorkerJob,
    WorkerRunRequest,
    WorkerTurnTelemetry,
)
from doxagent.codex_worker.sdk_runtime import CodexExecutionRuntime, TurnHandle
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore


class CapacityBusy(Exception):
    """No execution was dispatched or charged."""


class _Jobs:
    """No unbounded Python cache: SQLite is the durable indexed store."""

    def __init__(self, store: JobStore) -> None:
        self.store = store

    def get(self, key: str) -> WorkerJob | None:
        return self.store.get(key)

    def __getitem__(self, key: str) -> WorkerJob:
        job = self.get(key)
        if job is None:
            raise KeyError(key)
        return job

    def __setitem__(self, key: str, job: WorkerJob) -> None:
        self.store.save(job)


class WorkerJobManager:
    def __init__(
        self,
        runtime: CodexExecutionRuntime,
        workspaces: LocalWorkspaceStore,
        *,
        capacity: int = 2,
        queue_limit: int = 64,
        subagents: int = 0,
        pressure_enabled: bool = False,
    ) -> None:
        self._runtime = runtime
        self._workspaces = workspaces
        self.store = JobStore(workspaces.root)
        self._jobs = _Jobs(self.store)
        self.capacity, self.queue_limit, self.subagents = capacity, queue_limit, subagents
        self.generation = uuid4().hex
        self._active: dict[str, tuple[str, int]] = {}
        self._pending_finish: dict[str, dict[str, object]] = {}
        self._startup = asyncio.Semaphore(1)
        self._closed = False
        self._runtime_streak = 0
        self._probe_active = False
        self.pressure = PressureController()
        self.pressure_enabled = pressure_enabled
        self._sample_at = 0.0
        self._pressure_interruptions: list[float] = list(
            self.store.metadata("pressure_cooldown").get("interruptions", [])
        )
        self._cooldown_until = float(self.store.metadata("pressure_cooldown").get("until", 0))
        self._forced_reason: dict[str, str] = {}
        self._processes_recovered = not hasattr(runtime, "recover")
        self._pump: asyncio.Task[None] | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._handles: dict[str, TurnHandle] = {}
        self._condition = asyncio.Condition()
        self._resource_log = RotatingFileHandler(
            workspaces.root / "resource-samples.jsonl",
            maxBytes=4 * 1024**2,
            backupCount=3,
            encoding="utf-8",
            delay=True,
        )
        self._recover_snapshots()
        if hasattr(runtime, "receipt_callback"):
            runtime.receipt_callback = self._capture_identity

    async def _capture_identity(
        self, request: WorkerRunRequest, thread: str, turn: str | None
    ) -> None:
        for key in self._active:
            job = self._jobs[key]
            if job.run_id == request.run_id and job.attempt_id == request.attempt_id:
                await self._update(key, thread_id=thread, turn_id=turn)
                return

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
            self.start()
            return existing.model_copy(deep=True)
        if self._closed or len(self.store.queued()) >= self.queue_limit:
            raise CapacityBusy("RESOURCE_CAPACITY")
        job = WorkerJob(
            job_id=job_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            request_sha256=request_sha256,
            status="queued",
            wait_reason="RESOURCE_CAPACITY",
        )
        # Persist before the first await: duplicate HTTP submissions cannot dispatch twice.
        self.store.save(job, request)
        await blocking(self._persist, job)
        await self._emit(job.job_id, "job.queued", {"node": request.node.value})
        self._schedule()
        self.start()
        return job.model_copy(deep=True)

    def start(self) -> None:
        if self._pump is None and not self._closed:
            self._pump = asyncio.create_task(self._dispatch_loop())

    async def probe(self) -> dict[str, object]:
        probe = getattr(self._runtime, "probe", None)
        if probe is None:
            return {"provider_probe": "not_supported"}
        if (
            self._probe_active
            or not self._processes_recovered
            or self.pressure.paused
            or sum(w for _, w in self._active.values()) >= self.capacity
        ):
            return {"provider_probe": "deferred_capacity"}
        # Readiness must not create a third resident SDK/MCP stack or steal a job slot.
        self._probe_active = True
        quarantined = False
        try:
            return dict(await probe())
        except Exception as exc:
            quarantined = getattr(exc, "code", None) == "CAPSULE_CLEANUP_QUARANTINED"
            raise
        finally:
            self._probe_active = quarantined

    async def _dispatch_loop(self) -> None:
        if not self._processes_recovered:
            recover = getattr(self._runtime, "recover", None)
            if recover is not None:
                await recover()
            self._processes_recovered = True
            self._recover_snapshots()  # Completions may commit during old-process cleanup.
        while not self._closed:
            if self.pressure_enabled and time.monotonic() >= self._sample_at:
                value = await asyncio.to_thread(sample)
                self.pressure.update(value)
                self._resource_log.emit(
                    logging.makeLogRecord(
                        {
                            "msg": json.dumps(
                                {
                                    **asdict(value),
                                    "at": time.time(),
                                    "paused": self.pressure.paused,
                                    "occupied": sum(w for _, w in self._active.values()),
                                }
                            )
                        }
                    )
                )
                self._sample_at = time.monotonic() + 5
                self.store.metadata(
                    "pressure", {**asdict(value), "paused": self.pressure.paused, "at": time.time()}
                )
                measure = getattr(self._runtime, "memory_for", None)
                if measure is not None:
                    for key in list(self._tasks):
                        request = self.store.request(key)
                        if request is None:
                            continue
                        rss = await asyncio.to_thread(measure, request)
                        receipt = self._jobs[key].resource_receipt
                        await self._update(
                            key,
                            resource_receipt={
                                **receipt,
                                "rss_bytes": rss,
                                "peak_rss_bytes": max(rss, receipt.get("peak_rss_bytes", 0)),
                            },
                        )
                reap = getattr(self._runtime, "reap_idle", None)
                if self.pressure.paused and reap is not None:
                    reclaimed = await reap(all_idle=True)
                    if reclaimed:
                        value = await asyncio.to_thread(sample)
                        self.pressure.update(value)
                if self.pressure.extreme and self._tasks and not self._forced_reason:
                    candidates = [
                        key
                        for key in self._tasks
                        if key not in self._forced_reason
                        and self._jobs[key].execution_phase in {"STARTING", "RUNNING"}
                    ]
                    if candidates:

                        def priority(key: str) -> tuple[int, int]:
                            request = self.store.request(key)
                            measure = getattr(self._runtime, "memory_for", lambda _request: 0)
                            return (
                                int(
                                    request is not None
                                    and request.research_lane.value == "persistent_runtime"
                                ),
                                -measure(request) if request else 0,
                            )

                        victim = min(candidates, key=priority)
                        now = time.time()
                        self._pressure_interruptions = [
                            t for t in self._pressure_interruptions if now - t < 600
                        ] + [now]
                        if len(self._pressure_interruptions) >= 3:
                            self._cooldown_until = now + 600
                        self.store.metadata(
                            "pressure_cooldown",
                            {
                                "until": self._cooldown_until,
                                "interruptions": self._pressure_interruptions,
                            },
                        )
                        self._forced_reason[victim] = "WORKER_RESOURCE_PRESSURE"
                        self._tasks[victim].cancel()
            reap = getattr(self._runtime, "reap_idle", None)
            if reap is not None:
                await reap(all_idle=self.pressure.paused)
            self._schedule()
            await asyncio.sleep(0.25)

    def _schedule(self) -> None:
        if (
            self._closed
            or not self._processes_recovered
            or self.pressure.paused
            or time.time() < self._cooldown_until
        ):
            return
        for job_id in self.store.queued(prefer_runtime=self._runtime_streak < 3):
            if job_id in self._tasks:
                continue
            job = self._jobs[job_id]
            if job.next_retry_at > time.time():
                continue
            request = self.store.request(job_id)
            if request is None:
                continue
            effective_subagents = (
                min(self.subagents, request.max_subagents) if request.allow_subagents else 0
            )
            weight = 1 + effective_subagents
            effective_thread = (
                job.thread_id if job.infra_recovery_count and job.thread_id else request.thread_id
            )
            identity = effective_thread or request.run_id
            if sum(w for _, w in self._active.values()) + weight + int(
                self._probe_active
            ) > self.capacity or identity in {key for key, _ in self._active.values()}:
                continue
            self._active[job_id] = (identity, weight)
            self._runtime_streak = (
                self._runtime_streak + 1
                if request.research_lane.value == "persistent_runtime"
                else 0
            )
            effective = request.model_copy(
                update={
                    "allow_subagents": bool(effective_subagents),
                    "max_subagents": effective_subagents,
                    "thread_id": effective_thread,
                }
            )
            task = asyncio.create_task(self._execute(job_id, effective))
            self._tasks[job_id] = task
            task.add_done_callback(partial(self._task_done, job_id))
            if self._runtime_streak == 3:
                break  # Recompute research-first fairness on the next dispatcher tick.

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(job_id, None)
        # Cancellation before the coroutine's first instruction has no finally block.
        if task.cancelled() and self._jobs[job_id].execution_phase == "QUEUED":
            self._active.pop(job_id, None)
        if not task.cancelled():
            task.exception()  # Retrieve supervisor errors; durable status remains inspectable.

    async def _execute(self, job_id: str, request: WorkerRunRequest) -> None:
        try:
            await self._run(job_id, request)
        finally:
            cleanup_started = time.monotonic()
            await self._update(job_id, execution_phase="CLEANING")
            cleanup = getattr(self._runtime, "release", None)
            cleanup_ok = True
            if callable(cleanup):
                try:
                    await cleanup(request)
                except Exception as exc:
                    cleanup_ok = False
                    await self._update(job_id, cleanup_error=type(exc).__name__)
            finish = self._pending_finish.pop(
                job_id, {"status": "failed", "error_code": "WORKER_INTERRUPTED"}
            )
            if finish.get("error_code") == "CAPSULE_CLEANUP_QUARANTINED":
                cleanup_ok = False
            forced = self._forced_reason.pop(job_id, None)
            if forced:
                finish = {"status": "failed", "error_code": forced}
            if not cleanup_ok and finish.get("status") != "succeeded":
                finish = {
                    "status": "failed",
                    "error_code": "WORKER_INFRA_RECOVERY_EXHAUSTED",
                    "error_message": "owned process cleanup failed; "
                    "slot quarantined until manual reconciliation",
                }
            current = self._jobs[job_id]
            infrastructure = finish.get("error_code") in {
                "WORKER_PROCESS_EXITED",
                "WORKER_RESOURCE_PRESSURE",
                "WORKER_RESTARTED",
                "WORKER_MCP_UNAVAILABLE",
            }
            if infrastructure and cleanup_ok and not self._closed:
                if current.infra_recovery_count < 2:
                    finish = {
                        "status": "queued",
                        "infra_recovery_count": current.infra_recovery_count + 1,
                        "next_retry_at": time.time()
                        + (30 if current.infra_recovery_count == 0 else 120),
                        "wait_reason": "INFRASTRUCTURE_RECOVERY",
                        "error_code": None,
                    }
                else:
                    finish["error_code"] = "WORKER_INFRA_RECOVERY_EXHAUSTED"
            await self._update(
                job_id,
                **finish,
                finished_at=None if finish.get("status") == "queued" else utc_now(),
                execution_phase=("QUEUED" if finish.get("status") == "queued" else "SETTLED")
                if cleanup_ok
                else "CLEANUP_QUARANTINED",
                resource_receipt={
                    **current.resource_receipt,
                    "cleanup_ms": int((time.monotonic() - cleanup_started) * 1000),
                },
            )
            await blocking(self._persist_telemetry, request, self._jobs[job_id])
            if cleanup_ok:
                self._active.pop(job_id, None)

    async def close(self) -> None:
        self._closed = True
        if self._pump is not None:
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)
        for key, task in list(self._tasks.items()):
            if self._jobs[key].execution_phase != "CLEANING":
                task.cancel()
        await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
        close = getattr(self._runtime, "close", None)
        if close is not None:
            await close()
        self._resource_log.close()

    def get(self, job_id: str) -> WorkerJob | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    async def cancel(self, job_id: str) -> WorkerJob | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job.model_copy(deep=True)
        task = self._tasks.get(job_id)
        if task is not None:
            if job.execution_phase != "CLEANING":
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._jobs[job_id].status == "queued":
                self._active.pop(job_id, None)
                await self._update(
                    job_id, status="cancelled", execution_phase="SETTLED", finished_at=utc_now()
                )
        else:
            await self._update(
                job_id, status="cancelled", execution_phase="SETTLED", finished_at=utc_now()
            )
        return self._jobs[job_id].model_copy(deep=True)

    async def events(self, job_id: str, after_sequence: int = -1) -> AsyncIterator[WorkerEvent]:
        while True:
            if self._jobs.get(job_id) is None:
                return
            pending = self.store.events(job_id, after_sequence)
            for event in pending:
                after_sequence = event.sequence
                yield event
            if len(pending) == 128:
                continue
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
        phase = "START_THREAD"
        try:
            attempt_root = await blocking(
                self._workspaces.ensure_attempt, request.run_id, request.attempt_id
            )
            run_root = attempt_root.parents[1]
            await self._update(
                job_id,
                status="running",
                started_at=utc_now(),
                wait_reason=None,
                worker_generation=self.generation,
                execution_phase="STARTING",
            )
            phase = "START_THREAD"
            async with self._startup:
                handle = await asyncio.wait_for(
                    self._runtime.start(request, run_root),
                    timeout=min(120, request.timeout_seconds),
                )
            self._handles[job_id] = handle
            process = getattr(handle, "process", None)
            await self._update(
                job_id,
                execution_phase="RUNNING",
                thread_id=getattr(handle, "thread_id", None),
                turn_id=getattr(handle, "turn_id", None),
                resource_receipt={
                    "capsule_id": getattr(handle, "identity", None),
                    "pid": getattr(process, "pid", None),
                    "queue_wait_ms": max(
                        0, int((utc_now() - job.created_at).total_seconds() * 1000)
                    ),
                },
            )
            phase = "RUN_TURN"
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
                return
            await self._finish(
                job_id,
                status="succeeded",
                thread_id=result.thread_id,
                turn_id=result.turn_id,
                final_response=result.final_response,
                telemetry=telemetry,
            )
        except asyncio.CancelledError:
            active_handle = self._handles.get(job_id)
            if active_handle is not None and hasattr(active_handle, "interrupt"):
                with suppress(Exception):
                    await asyncio.wait_for(active_handle.interrupt(), timeout=5)
            await self._finish(job_id, status="cancelled")
            raise
        except TimeoutError:
            active_handle = self._handles.get(job_id)
            if active_handle is not None:
                with suppress(Exception):
                    await asyncio.wait_for(active_handle.interrupt(), timeout=5)
            await self._finish(
                job_id,
                status="failed",
                error_code="CODEX_START_TIMEOUT"
                if phase == "START_THREAD"
                else "CODEX_TURN_TIMEOUT",
                error_message=f"{phase} exceeded its timeout budget",
                telemetry=self._failure_telemetry(
                    started,
                    f"timeout:{request.timeout_seconds}s",
                ),
            )
        except Exception as exc:
            code = getattr(exc, "code", "CODEX_WORKER_ERROR")
            message = str(exc).lower()
            if (
                phase == "START_THREAD"
                and "mcp" in message
                and ("handshake" in message or "failed to initialize" in message)
            ):
                code = "WORKER_MCP_UNAVAILABLE"
            await self._finish(
                job_id,
                status="failed",
                error_code=code,
                error_message=f"{locals().get('phase', 'PREPARE')}: {exc}",
                telemetry=self._failure_telemetry(started, str(exc)),
            )
        finally:
            self._handles.pop(job_id, None)

    async def _update(self, job_id: str, **updates: object) -> None:
        job = self._jobs[job_id].model_copy(update={**updates, "updated_at": utc_now()})
        self._jobs[job_id] = job
        await blocking(self._persist, job)
        await self._emit(
            job_id,
            f"job.{job.status}",
            job.model_dump(mode="json", exclude={"telemetry", "final_response"}),
        )

    async def _finish(self, job_id: str, **updates: object) -> None:
        self._pending_finish[job_id] = updates

    async def _emit(self, job_id: str, event_type: str, payload: dict[str, object]) -> None:
        self.store.event(job_id, event_type, payload)
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

        for job_id in self.store.active():
            job = self._jobs[job_id]
            request = self.store.request(job_id)
            result_file = receipt_path(self._workspaces.root, request) if request else None
            if result_file is not None and result_file.exists():
                try:
                    result = json.loads(result_file.read_text(encoding="utf-8"))
                    success = result["status"].lower() in {"completed", "succeeded", "success"}
                    job = job.model_copy(
                        update={
                            "status": "succeeded" if success else "failed",
                            "execution_phase": "SETTLED",
                            "thread_id": result["thread_id"],
                            "turn_id": result["turn_id"],
                            "final_response": result.get("final_response"),
                            "error_code": None if success else "CODEX_TURN_FAILED",
                            "error_message": result.get("error_message"),
                            "finished_at": utc_now(),
                            "telemetry": WorkerTurnTelemetry.model_validate(result["telemetry"])
                            if result.get("telemetry")
                            else None,
                        }
                    )
                    self._jobs[job_id] = job
                    self._persist(job)
                    self.store.event(job_id, "job.result_recovered", {"status": job.status})
                    continue
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # Incomplete evidence does not assert successful execution.
            if job.status == "running" or self.store.request(job_id) is None:
                recoverable = (
                    self.store.request(job_id) is not None and job.infra_recovery_count < 2
                )
                job = job.model_copy(
                    update={
                        "status": "queued" if recoverable else "failed",
                        "execution_phase": "QUEUED" if recoverable else "SETTLED",
                        "wait_reason": "INFRASTRUCTURE_RECOVERY" if recoverable else None,
                        "infra_recovery_count": job.infra_recovery_count + int(recoverable),
                        "next_retry_at": time.time() + 30 if recoverable else 0,
                        "error_code": None if recoverable else "WORKER_INFRA_RECOVERY_EXHAUSTED",
                        "error_message": "worker restarted before the Codex turn completed",
                        "updated_at": utc_now(),
                    }
                )
                self._persist(job)
            self._jobs[job.job_id] = job
            self.store.event(job.job_id, "job.recovered", {"status": job.status})
