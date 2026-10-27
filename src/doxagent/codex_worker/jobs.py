"""In-process execution supervisor with durable job snapshots and event streams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from functools import partial
from pathlib import Path
from uuid import uuid4

from doxagent.codex_runtime.schema import utc_now
from doxagent.codex_worker.cleanup import await_owned
from doxagent.codex_worker.io_budget import blocking
from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.result_receipt import receipt_path
from doxagent.codex_worker.schema import (
    WorkerEvent,
    WorkerJob,
    WorkerRunRequest,
    WorkerTurnTelemetry,
)
from doxagent.codex_worker.sdk_runtime import CodexExecutionRuntime, TurnHandle
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.control_io import read_io, write_io
from doxagent.resource_safety import SafetyLevel, SafetyStateReader
from doxagent.w3_timing import BoundaryTrace, save_trace, snapshot_current, span


def _execution_lane(request: WorkerRunRequest) -> str:
    if request.execution_lane:
        return request.execution_lane
    if request.run_id.startswith("runtime-maintain-"):
        return "background"
    if request.research_lane.value == "persistent_runtime":
        return "realtime"
    return "background"


class QueueHighWatermark(Exception):
    """Durable storage protection, unrelated to execution concurrency."""


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
    EVENT_HEARTBEAT_SECONDS = 15

    def __init__(
        self,
        runtime: CodexExecutionRuntime,
        workspaces: LocalWorkspaceStore,
        *,
        queue_high_watermark: int = 10000,
        subagents: int = 0,
        launch_wave_size: int = 3,
        launch_interval_seconds: float = 2.0,
        startup_concurrency: int = 3,
        background_aging_seconds: float = 300,
        safety_state_path: Path | None = None,
        session_retention_settings=None,
    ) -> None:
        self._runtime = runtime
        self._workspaces = workspaces
        self.store = JobStore(workspaces.root)
        self._jobs = _Jobs(self.store)
        self.queue_high_watermark = queue_high_watermark
        self.subagents = subagents
        self.launch_wave_size = max(1, launch_wave_size)
        self.launch_interval_seconds = max(0.05, launch_interval_seconds)
        self.background_aging_seconds = max(1, background_aging_seconds)
        self.generation = uuid4().hex
        self._active: dict[str, str] = {}
        self._active_lanes: dict[str, str] = {}
        self._pending_finish: dict[str, dict[str, object]] = {}
        self.startup_concurrency = max(1, startup_concurrency)
        self._startup = asyncio.Semaphore(self.startup_concurrency)
        self._closed = False
        self._probe_active = False
        self.safety = SafetyStateReader(safety_state_path)
        self._reaper = None
        self._reap_error = None
        self._dispatcher_state = "STARTING"
        self._dispatcher_error = None
        self._dispatcher_progress = None
        self._dispatcher_retry_at = None
        self._dispatch_failures = 0
        self._observation_gc_task = None
        self._observation_gc_at = 0.0
        self._session_gc_at = time.monotonic() + 60
        self._settling = {}
        self._settle_retries = {}
        self._cleanup_quarantines = {}
        self._cleanup_recheck_at = 0.0
        if hasattr(runtime, "admission"):
            runtime.admission.safety = self.safety
            runtime.admission.handshake = self._startup
        self._forced_reason: dict[str, str] = {}
        self._critical_intervened = False
        self._next_launch_at = 0.0
        self._processes_recovered = not hasattr(runtime, "recover")
        self._pump: asyncio.Task[None] | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._starting: set[str] = set()
        self._handles: dict[str, TurnHandle] = {}
        from .thread_lifecycle import WorkerThreadLifecycle

        self.thread_lifecycle = WorkerThreadLifecycle(self, workspaces)
        self.session_retention = None
        self._retention_settings = session_retention_settings
        self._session_gc_task = None
        if session_retention_settings is not None:
            from .session_retention import TerminalSessions, business_holds

            self.session_retention = TerminalSessions(
                self,
                self.thread_lifecycle.transport,
                owner_holds=partial(business_holds, session_retention_settings),
            )
        self._condition = asyncio.Condition()
        self._submit_locks = {}
        self._schedule_lock = asyncio.Lock()
        self.loop_lag = {}
        self._lag_task = None
        self._recover_snapshots()
        if hasattr(runtime, "receipt_callback"):
            runtime.receipt_callback = self._capture_identity

    async def _capture_identity(
        self, request: WorkerRunRequest, thread: str, turn: str | None
    ) -> None:
        for key in self._active:
            job = await read_io(self.store.get, key)
            if job.run_id == request.run_id and job.attempt_id == request.attempt_id:
                await self._update(key, thread_id=thread, turn_id=turn)
                return

    async def submit(self, request: WorkerRunRequest) -> WorkerJob:
        key = (request.run_id, request.idempotency_key or request.attempt_id)
        entry = self._submit_locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                return await self._submit_owned(request)
        finally:
            entry[1] -= 1
            if not entry[1]:
                self._submit_locks.pop(key, None)

    async def _submit_owned(self, request: WorkerRunRequest) -> WorkerJob:
        hash_payload = request.model_dump(mode="json")
        for name in ("task_phase", "web_search_mode"):
            if hash_payload.get(name) is None:
                hash_payload.pop(name, None)
        request_sha256 = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True).encode()
        ).hexdigest()
        job_id = (
            hashlib.sha256(f"{request.run_id}:{request.idempotency_key}".encode()).hexdigest()
            if request.idempotency_key
            else uuid4().hex
        )
        existing = await read_io(self.store.get, job_id)
        if existing is not None:
            if existing.request_sha256 != request_sha256:
                raise ValueError("idempotency key reused for a different worker request")
            self.start()
            return existing.model_copy(deep=True)
        if self._closed or len(await read_io(self.store.queued)) >= self.queue_high_watermark:
            raise QueueHighWatermark("QUEUE_STORAGE_HIGH_WATERMARK")
        self.thread_lifecycle.validate_run(request)
        if self.session_retention is not None:
            self.session_retention.validate_submit(request)
            if (
                self._workspaces.root / request.run_id / "audit" / "retained-assets.json"
            ).is_file():
                raise ValueError("PUBLISHED_WORKSPACE_INPUTS_RETIRED_CREATE_NEW_RUN")
        job = WorkerJob(
            job_id=job_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            request_sha256=request_sha256,
            status="queued",
            wait_reason="LAUNCH_RAMP",
        )
        # Persist before the first await: duplicate HTTP submissions cannot dispatch twice.
        (await write_io(self.store.path, self.store.save, job, request, retry=True))
        await blocking(self._persist, job)
        await self._emit(job.job_id, "job.queued", {"node": request.node.value})
        await self._schedule()
        self.start()
        return job.model_copy(deep=True)

    def start(self) -> None:
        if not self._closed and (self._pump is None or self._pump.done()):
            if self._pump is not None and not self._pump.cancelled():
                self._pump.exception()
            self._pump = asyncio.create_task(self._dispatch_loop())

    def dispatcher_status(self):
        running = self._pump is not None and not self._pump.done()
        return dict(
            ready=running and self._processes_recovered and self._dispatcher_state == "RUNNING",
            running=running,
            state=self._dispatcher_state,
            processes_recovered=self._processes_recovered,
            last_progress_age_seconds=(
                time.monotonic() - self._dispatcher_progress
                if self._dispatcher_progress is not None
                else None
            ),
            last_error=self._dispatcher_error,
            retry_at=self._dispatcher_retry_at,
            settlements_pending=len(self._settle_retries),
        )

    async def _collect_owned_resources(self):
        from .workspace_retention import collect

        result = await self.session_retention.collect()
        result["workspaces"] = await asyncio.to_thread(
            collect, self._workspaces, self, self._retention_settings
        )
        from .snapshot_retention import collect as collect_snapshots

        result["initialization_snapshots"] = await asyncio.to_thread(
            collect_snapshots, self._workspaces, self, self._retention_settings
        )
        return result

    async def probe(self) -> dict[str, object]:
        probe = getattr(self._runtime, "probe", None)
        if probe is None:
            return {"provider_probe": "not_supported"}
        snapshot = self.safety.read()
        if (
            self._probe_active
            or not self._processes_recovered
            or snapshot.level is not SafetyLevel.NORMAL
        ):
            return {"provider_probe": "deferred_capacity"}
        # Readiness follows the cold-start ramp but does not consume long-lived capacity.
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
        while not self._closed:
            try:
                await self._dispatch_attempt()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._dispatcher_state = "RETRYING"
                self._dispatcher_error = f"{type(exc).__name__}: {str(exc)[:250]}"
                delay = (1, 2, 5, 10)[min(self._dispatch_failures, 3)]
                self._dispatch_failures += 1
                self._dispatcher_retry_at = time.time() + delay
                logging.getLogger(__name__).exception("Worker dispatcher will retry in %ss", delay)
                await asyncio.sleep(delay)

    def _observation_done(self, task):
        if not task.cancelled() and task.exception():
            logging.getLogger(__name__).warning("Observation GC failed: %s", task.exception())

    async def _dispatch_attempt(self):
        if self._lag_task is None:
            from doxagent.control_io import loop_watch

            self._lag_task = asyncio.create_task(loop_watch(self.loop_lag))
        if not self._processes_recovered:
            self._dispatcher_state = "RECOVERING"
            recover = getattr(self._runtime, "recover", None)
            if recover is not None:
                await recover()
            await read_io(
                self._recover_snapshots
            )  # Original completions may commit during cleanup.
            self._processes_recovered = True
        while not self._closed:
            if (
                self.session_retention is not None
                and time.monotonic() >= self._session_gc_at
                and (self._session_gc_task is None or self._session_gc_task.done())
            ):
                if self._session_gc_task is not None:
                    try:
                        if not self._session_gc_task.cancelled():
                            (
                                await write_io(
                                    self.store.path,
                                    self.store.metadata,
                                    "session_retention",
                                    self._session_gc_task.result(),
                                    retry=True,
                                )
                            )
                    except Exception as exc:
                        (
                            await write_io(
                                self.store.path,
                                self.store.metadata,
                                "session_retention",
                                {"error": str(exc)[:250]},
                                retry=True,
                            )
                        )
                self._session_gc_task = asyncio.create_task(self._collect_owned_resources())
                self._session_gc_at = time.monotonic() + 600
            if time.monotonic() >= self._observation_gc_at and (
                self._observation_gc_task is None or self._observation_gc_task.done()
            ):
                from .w3_turn_probe import maintenance

                self._observation_gc_task = asyncio.create_task(
                    asyncio.to_thread(maintenance, self.store.root)
                )
                self._observation_gc_task.add_done_callback(self._observation_done)
                self._observation_gc_at = time.monotonic() + 3600
            snapshot = self.safety.read()
            (
                await write_io(
                    self.store.path,
                    self.store.metadata,
                    "safety",
                    {
                        "level": snapshot.level.value,
                        "observed_at": snapshot.observed_at,
                        "reasons": list(snapshot.reasons),
                        "stale": snapshot.stale,
                    },
                    retry=True,
                )
            )
            if snapshot.level is not SafetyLevel.CRITICAL:
                self._critical_intervened = False
            if (
                snapshot.level is SafetyLevel.CRITICAL
                and self._tasks
                and not self._critical_intervened
            ):
                candidates = [
                    key
                    for key in self._tasks
                    if self._active_lanes.get(key) == "background"
                    and (await read_io(self.store.get, key)).execution_phase
                    in {"STARTING", "RUNNING"}
                ]
                if candidates:
                    measure = getattr(self._runtime, "memory_for", lambda _request: 0)
                    sizes = {}
                    for key in candidates:
                        active_request = await read_io(self.store.request, key)
                        sizes[key] = (
                            await read_io(measure, active_request)
                            if active_request is not None
                            else 0
                        )
                    victim = max(candidates, key=sizes.get)
                    self._critical_intervened = True
                    self._forced_reason[victim] = "WORKER_RESOURCE_CRITICAL"
                    self._tasks[victim].cancel()
            reap = getattr(self._runtime, "reap_idle", None)
            if reap is not None and (self._reaper is None or self._reaper.done()):
                if self._reaper is not None:
                    error = None if self._reaper.cancelled() else self._reaper.exception()
                    if error:
                        self._reap_error = type(error).__name__
                kwargs = {"all_idle": snapshot.level is SafetyLevel.CRITICAL}
                if getattr(self._runtime, "manages_startup_capacity", False):
                    kwargs["pressure"] = snapshot.level is SafetyLevel.PRESSURE
                self._reaper = asyncio.create_task(reap(**kwargs))
            warm = getattr(self._runtime, "warm", None)
            if warm is not None and snapshot.level is SafetyLevel.NORMAL:
                await warm(self.launch_wave_size, self._startup, self.launch_interval_seconds)
            self._retry_settlements()
            helper = self.thread_lifecycle.transport
            if (
                self._cleanup_quarantines or getattr(helper, "quarantined", False)
            ) and time.monotonic() >= self._cleanup_recheck_at:
                self._cleanup_recheck_at = time.monotonic() + 30
                await self._reconcile_cleanups()
                reconcile = getattr(helper, "reconcile_cleanup", None)
                if reconcile is not None:
                    await reconcile()
            await self._schedule()
            self._dispatcher_state = "RUNNING"
            self._dispatcher_progress = time.monotonic()
            self._dispatcher_retry_at = None
            self._dispatch_failures = 0
            await asyncio.sleep(0.25)

    async def _schedule(self) -> None:
        async with self._schedule_lock:
            await self._schedule_owned()

    async def _schedule_owned(self) -> None:
        if self._closed or not self._processes_recovered:
            return
        now = time.monotonic()
        snapshot = self.safety.read()
        realtime = await read_io(self.store.queued, lane="realtime")
        background = await read_io(self.store.queued, lane="background")
        if snapshot.level is SafetyLevel.CRITICAL:
            await self._mark_waiting(realtime + background, "SAFETY_CRITICAL")
            return
        if snapshot.level is SafetyLevel.PRESSURE:
            await self._mark_waiting(background, "SAFETY_PRESSURE")
            queued = realtime
            wave_size = 1
        else:
            oldest_background = (
                (await read_io(self.store.get, background[0])) if background else None
            )
            aged = bool(
                oldest_background
                and (utc_now() - oldest_background.created_at).total_seconds()
                >= self.background_aging_seconds
            )
            if aged and realtime:
                queued = realtime[: self.launch_wave_size - 1] + background[:1]
                queued += realtime[self.launch_wave_size - 1 :] + background[1:]
            else:
                queued = realtime + background
            wave_size = self.launch_wave_size
        launched = 0
        wave_size = min(
            wave_size,
            max(0, self.startup_concurrency - len(self._starting)),
        )
        for job_id in queued:
            if job_id in self._tasks or job_id in self._settle_retries or job_id in self._settling:
                continue
            job = await read_io(self.store.get, job_id)
            if job.next_retry_at > time.time():
                continue
            request = await read_io(self.store.request, job_id)
            if request is None:
                continue
            # W1/W2/W3 and their required preparation keep the existing
            # admission policy. Only deferrable research/initialization waits.
            if request.research_lane.value != "persistent_runtime":
                from doxagent.message_bus_v2.pressure import background_allowed

                if not background_allowed():
                    await self._mark_waiting([job_id], "MESSAGE_PIPELINE_RESOURCE_RESERVE")
                    continue
            warm = str(request.node) == "persistent_runtime_w3" and getattr(
                self._runtime,
                "can_start_warm",
                lambda _: False,
            )(request.thread_id)
            if not warm and (launched >= wave_size or now < self._next_launch_at):
                continue
            effective_subagents = (
                min(self.subagents, request.max_subagents) if request.allow_subagents else 0
            )
            lane = _execution_lane(request)
            effective_thread = (
                job.thread_id if job.infra_recovery_count and job.thread_id else request.thread_id
            )
            # Only an existing Codex thread is thread-affine. Independent attempts in
            # one workflow run use isolated attempt workspaces and may execute together.
            identity = effective_thread or f"{request.run_id}:{request.attempt_id}"
            if identity in set(self._active.values()):
                continue
            await write_io(
                self.store.path,
                self.store.save,
                job.model_copy(
                    update={
                        "wait_reason": None,
                        "resource_receipt": {
                            **job.resource_receipt,
                            "execution_lane": lane,
                            "dispatch_safety_level": snapshot.level.value,
                        },
                    }
                ),
                retry=True,
            )
            effective = request.model_copy(
                update={
                    "allow_subagents": bool(effective_subagents),
                    "max_subagents": effective_subagents,
                    "thread_id": effective_thread,
                }
            )
            self._active[job_id] = identity
            self._active_lanes[job_id] = lane
            task = asyncio.create_task(self._execute(job_id, effective))
            self._tasks[job_id] = task
            self._starting.add(job_id)
            task.add_done_callback(partial(self._task_done, job_id))
            launched += int(not warm)
        if launched:
            self._next_launch_at = now + self.launch_interval_seconds

    async def _mark_waiting(self, job_ids: list[str], reason: str) -> None:
        for job_id in job_ids:
            job = await read_io(self.store.get, job_id)
            if job is not None and job.wait_reason != reason:
                await write_io(
                    self.store.path,
                    self.store.save,
                    job.model_copy(update={"wait_reason": reason}),
                    retry=True,
                )

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(job_id, None)
        # Cancellation before the coroutine's first instruction has no finally block.
        if task.cancelled() and (self.store.get(job_id)).execution_phase == "QUEUED":
            self._active.pop(job_id, None)
            self._active_lanes.pop(job_id, None)
        self._starting.discard(job_id)
        if not task.cancelled():
            task.exception()  # Retrieve supervisor errors; durable status remains inspectable.

    async def _execute(self, job_id: str, request: WorkerRunRequest) -> None:
        await self._execute_leased(job_id, request)

    async def _execute_leased(self, job_id: str, request: WorkerRunRequest) -> None:
        try:
            await self._run(job_id, request)
        except asyncio.CancelledError:
            # Off-loop preflight may be cancelled before _run_observed enters
            # its try/finally. Keep that cancellation in the same owner scope.
            self._pending_finish.setdefault(job_id, {"status": "cancelled"})
            raise
        finally:
            await await_owned(self._launch_settlement(job_id, request))

    def _launch_settlement(self, job_id, request):
        task = self._settling.get(job_id)
        if task is None or task.done():
            task = asyncio.create_task(self._settle_execution(job_id, request))
            self._settling[job_id] = task
            task.add_done_callback(partial(self._settlement_done, job_id))
        return task

    def _settlement_done(self, job_id, task):
        if self._settling.get(job_id) is task:
            self._settling.pop(job_id, None)
        if not task.cancelled():
            task.exception()

    def _retry_settlements(self):
        for job_id, retry in list(self._settle_retries.items()):
            if retry["at"] > time.monotonic() or job_id in self._settling:
                continue
            request = self.store.request(job_id)
            if request is not None:
                self._launch_settlement(job_id, request)
            elif (self.store.get(job_id)).status in {"succeeded", "failed", "cancelled"}:
                # ACK can remove the request after the terminal SQL commit but
                # before a failed mirror/event acknowledgement is retried.
                if (self.store.get(job_id)).execution_phase == "SETTLED":
                    self._active.pop(job_id, None)
                    self._active_lanes.pop(job_id, None)
                self._settle_retries.pop(job_id, None)
                self._pending_finish.pop(job_id, None)

    async def _reconcile_cleanups(self):
        complete = getattr(self._runtime, "cleanup_complete_for", None)
        if complete is None:
            return
        for job_id, key in list(self._cleanup_quarantines.items()):
            request = await read_io(self.store.request, job_id)
            complete_key = getattr(self._runtime, "cleanup_complete_key", None)
            gone = (
                complete(request)
                if request is not None
                else key is not None and complete_key is not None and complete_key(key)
            )
            if gone:
                await self._update(job_id, execution_phase="SETTLED")
                self._active.pop(job_id, None)
                self._active_lanes.pop(job_id, None)
                self._cleanup_quarantines.pop(job_id, None)

    async def _settle_execution(self, job_id, request):
        try:
            cleanup_started = time.monotonic()
            try:
                await self._update(job_id, execution_phase="CLEANING")
            except Exception:
                logging.getLogger(__name__).warning("Cleaning marker failed for %s", job_id)
            cleanup = getattr(self._runtime, "release", None)
            cleanup_ok = True
            if callable(cleanup):
                try:
                    await cleanup(request)
                except Exception as exc:
                    cleanup_ok = False
                    logging.getLogger(__name__).warning("Job %s cleanup: %s", job_id, exc)
            finish = dict(
                self._pending_finish.get(
                    job_id, {"status": "failed", "error_code": "WORKER_INTERRUPTED"}
                )
            )
            planned = finish.pop("_settlement_planned", False)
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
                    "slot quarantined until owned-process exit is confirmed",
                }
            current = await read_io(self.store.get, job_id)
            startup_failure = getattr(self._runtime, "failure_for", lambda _: None)(request)
            if startup_failure:
                await self._update(
                    job_id,
                    resource_receipt={
                        **current.resource_receipt,
                        "startup_failure": startup_failure,
                    },
                )
                current = await read_io(self.store.get, job_id)
            safe_start_retry = (
                finish.get("error_code") == "CODEX_START_TIMEOUT"
                and startup_failure is not None
                and startup_failure.get("run_command_sent") is False
            )
            infrastructure = safe_start_retry or finish.get("error_code") in {
                "WORKER_PROCESS_EXITED",
                "WORKER_RESOURCE_PRESSURE",
                "WORKER_RESOURCE_CRITICAL",
                "WORKER_RESTARTED",
                "WORKER_MCP_UNAVAILABLE",
            }
            if infrastructure and cleanup_ok and not self._closed and not planned:
                retry_limit = (
                    1
                    if startup_failure is not None
                    and startup_failure.get("run_command_sent") is False
                    else 2
                )
                if current.infra_recovery_count < retry_limit:
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
            observation = finish.pop("w3_observation", None)
            # A mirror/event write can fail after SQLite committed. Retrying the
            # settlement must not spend another infrastructure retry generation.
            self._pending_finish[job_id] = {
                **finish,
                "_settlement_planned": True,
                "w3_observation": observation,
            }
            if not cleanup_ok:
                # Only retain the physical lookup key, never a second prompt copy.
                key_for = getattr(self._runtime, "key", lambda _: None)
                self._cleanup_quarantines[job_id] = key_for(request)
            await self._update(
                job_id,
                **finish,
                finished_at=None
                if finish.get("status") == "queued"
                else (current.finished_at or utc_now()),
                execution_phase=("QUEUED" if finish.get("status") == "queued" else "SETTLED")
                if cleanup_ok
                else "CLEANUP_QUARANTINED",
                resource_receipt={
                    **current.resource_receipt,
                    **({"w3_observation": observation} if observation is not None else {}),
                    "cleanup_ms": int((time.monotonic() - cleanup_started) * 1000),
                },
            )
            if cleanup_ok:
                self._active.pop(job_id, None)
                self._active_lanes.pop(job_id, None)
            self._pending_finish.pop(job_id, None)
            self._settle_retries.pop(job_id, None)
            try:
                await blocking(
                    self._persist_telemetry, request, (await read_io(self.store.get, job_id))
                )
            except Exception:
                logging.getLogger(__name__).warning("Terminal job telemetry failed for %s", job_id)
        except Exception as exc:
            attempts = self._settle_retries.get(job_id, {}).get("attempts", 0)
            self._settle_retries[job_id] = dict(
                attempts=attempts + 1,
                at=time.monotonic() + (1, 2, 5, 10)[min(attempts, 3)],
                error=f"{type(exc).__name__}: {str(exc)[:250]}",
            )
            logging.getLogger(__name__).exception("Job %s finalization will retry", job_id)
            raise

    async def close(self) -> None:
        if self._lag_task is not None:
            self._lag_task.cancel()
            await asyncio.gather(self._lag_task, return_exceptions=True)
        self._closed = True
        self._dispatcher_state = "STOPPED"
        if self._observation_gc_task is not None:
            self._observation_gc_task.cancel()
            await asyncio.gather(self._observation_gc_task, return_exceptions=True)
        if self._session_gc_task is not None:
            self._session_gc_task.cancel()
            await asyncio.gather(self._session_gc_task, return_exceptions=True)
        if self._pump is not None:
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)
        for key, task in list(self._tasks.items()):
            if (await read_io(self.store.get, key)).execution_phase != "CLEANING":
                task.cancel()
        await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
        await self.thread_lifecycle.close()
        if self._reaper is not None:
            await asyncio.gather(self._reaper, return_exceptions=True)
        close = getattr(self._runtime, "close", None)
        if close is not None:
            await close()

    def get(self, job_id: str) -> WorkerJob | None:
        job = self.store.get(job_id)
        return job.model_copy(deep=True) if job else None

    def resources(self) -> dict[str, object]:
        from doxagent.control_io import diagnostic_status

        snapshot = self.safety.read()
        now = utc_now()
        admission = getattr(self._runtime, "admission", None)
        cold = getattr(admission, "cold", None)
        active_by_lane = {
            lane: sum(1 for value in self._active_lanes.values() if value == lane)
            for lane in ("realtime", "background")
        }
        return {
            "dispatcher": "elastic",
            "event_loop_lag": dict(self.loop_lag),
            "diagnostics": diagnostic_status(),
            "active_executions": len(self._active),
            "cold_starts": len(self._starting),
            "active_by_lane": active_by_lane,
            "queued": len(self.store.queued()),
            "queued_by_lane": self.store.queued_counts(),
            "oldest_queue_wait_seconds": {
                lane: (
                    max(0.0, (now - oldest.created_at).total_seconds())
                    if (oldest := self.store.oldest_queued(lane)) is not None
                    else 0.0
                )
                for lane in ("realtime", "background")
            },
            "launch_ramp": {
                "wave_size": self.launch_wave_size,
                "interval_seconds": self.launch_interval_seconds,
                "startup_concurrency": self.startup_concurrency,
            },
            "safety": {
                "level": snapshot.level.value,
                "observed_at": snapshot.observed_at,
                "reasons": list(snapshot.reasons),
                "stale": snapshot.stale,
            },
            "startup_admission": {
                "cold_active": getattr(cold, "active", False),
                "cold_waiters": len(getattr(cold, "waiters", [])),
                "cold_owner": cold.snapshot() if cold is not None else None,
                "cold_quarantined_owners": list(getattr(admission, "quarantined", ()))[:8],
                "startup_pressure": getattr(admission, "startup_pressure", False),
                "reap_error": self._reap_error,
            },
            "dispatcher_running": self._pump is not None and not self._pump.done(),
            "dispatcher_status": self.dispatcher_status(),
            "cleanup": getattr(self._runtime, "cleanup_snapshot", lambda: {})(),
            "lifecycle": getattr(self.thread_lifecycle.transport, "snapshot", lambda: {})(),
            "generation": self.generation,
        }

    async def cancel(self, job_id: str) -> WorkerJob | None:
        job = await read_io(self.store.get, job_id)
        if job is None:
            return None
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job.model_copy(deep=True)
        task = self._tasks.get(job_id)
        if task is not None:
            if job.execution_phase != "CLEANING":
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if (await read_io(self.store.get, job_id)).status == "queued":
                self._active.pop(job_id, None)
                self._active_lanes.pop(job_id, None)
                await self._update(
                    job_id, status="cancelled", execution_phase="SETTLED", finished_at=utc_now()
                )
        else:
            await self._update(
                job_id, status="cancelled", execution_phase="SETTLED", finished_at=utc_now()
            )
        return (await read_io(self.store.get, job_id)).model_copy(deep=True)

    async def events(self, job_id: str, after_sequence: int = -1) -> AsyncIterator[WorkerEvent]:
        while True:
            if await read_io(self.store.get, job_id) is None:
                return
            pending = await read_io(self.store.events, job_id, after_sequence)
            for event in pending:
                after_sequence = event.sequence
                yield event
            if len(pending) == 128:
                continue
            if (await read_io(self.store.get, job_id)).status in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                return
            async with self._condition:
                timed_out = False
                try:
                    async with asyncio.timeout(self.EVENT_HEARTBEAT_SECONDS):
                        await self._condition.wait()
                except TimeoutError:
                    timed_out = True
            if timed_out:
                yield WorkerEvent(sequence=after_sequence, event_type="heartbeat", payload={})

    async def _run(self, job_id: str, request: WorkerRunRequest) -> None:
        if str(request.node) != "persistent_runtime_w3":
            return await self._run_observed(job_id, request)
        trace = BoundaryTrace(
            job_id=job_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            phase=request.task_phase,
            worker_generation=self.generation,
            worker_queue_to_run_ms=max(
                0,
                round(
                    (utc_now() - (await read_io(self.store.get, job_id)).created_at).total_seconds()
                    * 1000,
                    3,
                ),
            ),
        )
        with trace.activate():
            try:
                return await self._run_observed(job_id, request)
            finally:
                await read_io(save_trace, trace, self.store.root / "boundary-timing")

    async def _run_observed(self, job_id: str, request: WorkerRunRequest) -> None:
        job = await read_io(self.store.get, job_id)
        started = time.monotonic()
        phase = "START_THREAD"
        try:
            with span("worker.ensure_attempt"):
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
            cold_start_started = time.monotonic()
            try:
                managed = getattr(self._runtime, "manages_startup_capacity", False)
                with span("worker.startup_wait"):
                    if not managed:
                        await self._startup.acquire()
                try:
                    with span("worker.runtime_start"):
                        async with asyncio.timeout(
                            max(0.001, min(120, request.timeout_seconds) - (15 if managed else 0))
                        ):
                            handle = await self._runtime.start(request, run_root)
                finally:
                    if not managed:
                        self._startup.release()
            finally:
                self._starting.discard(job_id)
            self._handles[job_id] = handle
            if self._closed or asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            process = getattr(handle, "process", None)
            await self._update(
                job_id,
                execution_phase="RUNNING",
                thread_id=getattr(handle, "thread_id", None),
                turn_id=getattr(handle, "turn_id", None),
                resource_receipt={
                    "capsule_id": getattr(handle, "identity", None),
                    "pid": getattr(process, "pid", None),
                    "cold_start_ms": int((time.monotonic() - cold_start_started) * 1000),
                    "queue_wait_ms": max(
                        0, int((utc_now() - job.created_at).total_seconds() * 1000)
                    ),
                    **(
                        {
                            "startup_timing": snapshot_current(),
                            "capsule_startup_timing": getattr(handle, "startup_timing", None),
                        }
                        if str(request.node) == "persistent_runtime_w3"
                        else {}
                    ),
                },
            )
            phase = "RUN_TURN"
            # Capsule owns the SDK timer. Parent supervises the same deadline
            # plus physical handoff, never starts another full budget after SQL.
            deadline = getattr(handle, "sdk_deadline", None)
            remaining = (
                max(0.001, deadline + 15 - time.monotonic())
                if deadline
                else request.timeout_seconds
            )
            async with asyncio.timeout(remaining):
                result = await handle.run()
            if getattr(result, "execution_timing", None):
                current = await read_io(self.store.get, job_id)
                await self._update(
                    job_id,
                    resource_receipt={
                        **current.resource_receipt,
                        "execution_timing": result.execution_timing,
                    },
                )
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
                    w3_observation=getattr(result, "observation", None),
                )
                return
            await self._finish(
                job_id,
                status="succeeded",
                thread_id=result.thread_id,
                turn_id=result.turn_id,
                final_response=result.final_response,
                telemetry=telemetry,
                w3_observation=getattr(result, "observation", None),
            )
        except asyncio.CancelledError:
            active_handle = self._handles.get(job_id)
            if active_handle is not None and hasattr(active_handle, "interrupt"):
                with suppress(Exception):
                    await asyncio.wait_for(active_handle.interrupt(), timeout=5)
            await self._finish(job_id, status="cancelled")
            raise
        except TimeoutError:
            # Completion is authoritative even if RUNNING/event persistence or
            # pipe delivery crossed the caller's deadline. Never repeat that turn.
            def completed_receipt():
                path = receipt_path(self._workspaces.root, request)
                if not path.is_file():
                    return None
                data = json.loads(path.read_text(encoding="utf8"))
                timing = data.get("execution_timing") or {}
                before_deadline = (
                    timing.get("sdk_completed_monotonic") is not None
                    and timing.get("sdk_deadline_monotonic") is not None
                    and timing["sdk_completed_monotonic"] <= timing["sdk_deadline_monotonic"]
                )
                return (
                    data
                    if data.get("status") in {"completed", "succeeded", "success"}
                    and before_deadline
                    else None
                )

            recovered = await read_io(completed_receipt)
            if recovered is not None:
                await self._finish(
                    job_id,
                    status="succeeded",
                    thread_id=recovered["thread_id"],
                    turn_id=recovered["turn_id"],
                    final_response=recovered.get("final_response"),
                    telemetry=WorkerTurnTelemetry.model_validate(recovered["telemetry"])
                    if recovered.get("telemetry")
                    else None,
                )
                return
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
        def update_record():
            job = self.store.get(job_id).model_copy(update={**updates, "updated_at": utc_now()})
            self.store.save(job)
            return job

        job = await write_io(self.store.path, update_record, priority=0, retry=True)
        await blocking(self._persist, job)
        await self._emit(
            job_id,
            f"job.{job.status}",
            job.model_dump(mode="json", exclude={"telemetry", "final_response"}),
        )

    async def _finish(self, job_id: str, **updates: object) -> None:
        self._pending_finish[job_id] = updates

    async def _emit(self, job_id: str, event_type: str, payload: dict[str, object]) -> None:
        from doxagent.control_io import write_io

        await write_io(self.store.path, self.store.event, job_id, event_type, payload, retry=True)
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
            "w3_observation": job.resource_receipt.get("w3_observation"),
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
                            "resource_receipt": {
                                **job.resource_receipt,
                                **(
                                    {"w3_observation": result["observation"]}
                                    if result.get("observation") is not None
                                    else {}
                                ),
                            },
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
