"""Thread-affine SDK process capsules with elastic active concurrency."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from doxagent.codex_runtime.schema import CodexAgentRole, ResearchLane
from doxagent.w3_timing import observed, span

from .cleanup import await_owned
from .process_tree import owned_processes, signal_owned, terminate_owned
from .result_receipt import receipt_path
from .schema import WorkerRunRequest, WorkerTurnTelemetry
from .sdk_runtime import WorkerTurnResult
from .startup_admission import StartupAdmission, progress, remaining, startup_scope


class CapsuleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Capsule:
    GRACEFUL_CLOSE_SECONDS = 1
    CLEANUP_TIMEOUT_SECONDS = 15

    def __init__(self, root: Path, boot_timeout: float = 90) -> None:
        self.identity = uuid4().hex
        self.root = root
        self.process: asyncio.subprocess.Process | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.busy = True
        self.healthy = False
        self.idle_at = 0.0
        self.ready_slot = False
        self._log: Any = None
        self._stderr_task: asyncio.Task[None] | None = None
        self.stderr_bytes = 0
        self.startup_timing: dict[str, Any] | None = None
        self.boot_timing: dict[str, Any] | None = None
        self.prepared = False
        self.boot_timeout = boot_timeout
        self.handoff_until = 0.0
        self.close_reason = None
        self.run_command_sent = False
        self.cleanup_state = None
        self.cleanup_error = None
        self.cleanup_started_at = None
        self.cleanup_retry_at = 0.0
        self._cleanup_task = None

    @observed("capsule.spawn")
    async def launch(self) -> None:
        directory = self.root / "capsules" / self.identity
        directory.mkdir(parents=True)
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "doxagent.codex_worker.capsule_child",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "DOXAGENT_CAPSULE_ID": self.identity,
                "DOXAGENT_MCP_BUDGET_ROOT": str(directory / "tool-slots"),
            },
            start_new_session=os.name != "nt",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            limit=16 * 1024 * 1024,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        owned = await asyncio.to_thread(owned_processes, self.identity)
        (directory / "owner.json").write_text(
            json.dumps(
                {
                    "pid": self.process.pid,
                    "identity": self.identity,
                    "pgid": self.process.pid if os.name == "posix" else None,
                    "start_ticks": owned.get(self.process.pid, (None, 0))[0],
                }
            ),
            encoding="utf-8",
        )
        await progress("BOOTING", capsule_id=self.identity)
        self.boot_timing = dict(
            capsule_id=self.identity, pid=self.process.pid, stage="PROCESS_STARTED"
        )
        try:
            async with asyncio.timeout(remaining(self.boot_timeout)):
                while True:
                    boot = await self.receive()
                    if boot.get("type") == "boot_progress":
                        self.boot_timing.update(boot)
                        await progress(
                            "BOOTING", capsule_id=self.identity, startup_receipt=self.boot_timing
                        )
                        continue
                    if boot.get("type") != "boot_ready" or boot.get("state") != "SDK_READY":
                        raise CapsuleError("CAPSULE_BOOT_PROTOCOL", "expected SDK_READY")
                    self.boot_timing.update(
                        stage="SDK_READY",
                        timing=boot.get("timing"),
                        boot_slow=(boot.get("timing", {}).get("sdk_ready_ms", 0) > 30000),
                    )
                    await progress(
                        "SDK_READY", capsule_id=self.identity, startup_receipt=self.boot_timing
                    )
                    break
        except BaseException as exc:
            self.close_reason = type(exc).__name__
            self.boot_timing.update(
                returncode=self.process.returncode, parent_end_reason=self.close_reason
            )
            self.capture_process()
            raise

    def capture_process(self):
        if self.process is None:
            return
        self.boot_timing = self.boot_timing or {}
        self.boot_timing.update(
            returncode=self.process.returncode,
            parent_end_reason=self.close_reason,
            stderr_bytes=self.stderr_bytes,
        )
        try:
            base = Path("/proc") / str(self.process.pid)
            self.boot_timing["schedstat"] = (base / "schedstat").read_text().strip()
            fields = (base / "stat").read_text().rsplit(")", 1)[1].split()
            self.boot_timing["cpu_ticks"] = int(fields[11]) + int(fields[12])
            self.boot_timing["start_ticks"] = fields[19]
        except (OSError, IndexError, ValueError):
            pass

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        # Raw SDK stderr may contain credentials/tool payloads. Structured protocol
        # failures remain in job receipts; do not retain a second unbounded log.
        while chunk := await self.process.stderr.read(8192):
            self.stderr_bytes += len(chunk)

    async def send(self, payload: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        line = await self.process.stdout.readline()
        if not line:
            raise CapsuleError("WORKER_PROCESS_EXITED", "owned SDK process exited")
        payload: dict[str, Any] = json.loads(line)
        if payload["type"] == "error":
            raise CapsuleError(payload["code"], payload["message"])
        return payload

    async def run(self) -> WorkerTurnResult:
        while True:
            payload = await self.receive()
            if payload["type"] == "result":
                data = payload["result"]
                if data.get("telemetry") is not None:
                    data["telemetry"] = WorkerTurnTelemetry.model_validate(data["telemetry"])
                self.healthy = True
                self.last_result_status = data.get("status")
                return WorkerTurnResult(**data)

    async def interrupt(self) -> None:
        self.healthy = False
        await self.send({"type": "interrupt"})

    async def close(self) -> None:
        if self._cleanup_task is None or (
            self._cleanup_task.done()
            and self.cleanup_state == "QUARANTINED"
            and time.monotonic() >= self.cleanup_retry_at
        ):
            self.healthy, self.busy, self.ready_slot = False, True, False
            self.cleanup_state = "CLOSING"
            self.cleanup_started_at = time.monotonic()
            self._cleanup_task = asyncio.create_task(self._close_owned())
        await await_owned(self._cleanup_task)

    def _record_cleanup(self):
        # Reuse the small owner record. Diagnostics must never prevent a kill.
        target = self.root / "capsules" / self.identity / "owner.json"
        try:
            value = (
                json.loads(target.read_text())
                if target.exists()
                else {"identity": self.identity, "pid": getattr(self.process, "pid", None)}
            )
            if not isinstance(value, dict):
                value = {"identity": self.identity, "pid": getattr(self.process, "pid", None)}
            value.update(
                cleanup_state=self.cleanup_state,
                cleanup_error=self.cleanup_error,
                close_reason=self.close_reason,
            )
            if target.parent.is_dir():
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps(value), encoding="utf-8")
                temporary.replace(target)
        except (OSError, ValueError):
            pass

    async def _close_owned(self):
        self._record_cleanup()
        try:
            async with asyncio.timeout(self.CLEANUP_TIMEOUT_SECONDS):
                await self._close_process()
        except BaseException as exc:
            self.cleanup_state = "QUARANTINED"
            self.cleanup_error = f"{type(exc).__name__}: {str(exc)[:250]}"
            self.cleanup_retry_at = time.monotonic() + 30
            self._record_cleanup()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise CapsuleError(
                "CAPSULE_CLEANUP_QUARANTINED", "owned process cleanup failed"
            ) from exc
        self.cleanup_state = "CLOSED"
        self.cleanup_error = None
        self._record_cleanup()

    async def _close_process(self):
        self.capture_process()
        if self.process is not None:
            if self.process.returncode is None:
                try:
                    async with asyncio.timeout(self.GRACEFUL_CLOSE_SECONDS):
                        await self.send({"type": "close"})
                        await self.process.wait()
                except Exception:
                    pass
            await terminate_owned(self.process, self.identity)
            self.capture_process()
        if self._log is not None:
            self._log.close()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)


class CapsuleRuntime:
    manages_startup_capacity = True

    def __init__(self, root: Path) -> None:
        self.root = root
        self.pool: list[Capsule] = []
        self.active: dict[str, Capsule] = {}
        self._lock = asyncio.Lock()
        self.receipt_callback: Any = None
        self._warming: set[asyncio.Task] = set()
        self._warm_next_at = 0.0
        self._warm_failed_until = 0.0
        self._warm_failures = 0
        self.admission = StartupAdmission()
        self.boot_timeout = 90
        self.prepare_timeout = 45
        self.handoff_seconds = 90
        self._failures = {}

    async def recover(self) -> None:
        import signal

        # The caller already holds the exclusive worker-directory OS lock.
        owners = list((self.root / "capsules").glob("*/owner.json"))
        owners += list((self.root / "lifecycle-owners").glob("*.json"))
        for owner in owners:
            identity = json.loads(owner.read_text(encoding="utf-8"))["identity"]
            for _ in range(50):
                remaining = await asyncio.to_thread(owned_processes, identity)
                if not remaining:
                    break
                await asyncio.to_thread(signal_owned, remaining, signal.SIGKILL)
                await asyncio.sleep(0.1)
            else:
                raise CapsuleError("CAPSULE_CLEANUP_QUARANTINED", "old execution still exists")

    def memory_for(self, request: WorkerRunRequest) -> int:
        capsule = self.active.get(self.key(request))
        return sum(rss for _, rss in owned_processes(capsule.identity).values()) if capsule else 0

    @staticmethod
    def key(request: WorkerRunRequest) -> str:
        return request.run_id + ":" + (request.idempotency_key or request.attempt_id)

    @staticmethod
    def live(c):
        return (
            c.healthy
            and not getattr(c, "cleanup_state", None)
            and getattr(getattr(c, "process", None), "returncode", None) is None
        )

    async def _dispose(self, capsule, *, cold=False):
        try:
            await capsule.close()
            capsule.cleanup_state = "CLOSED"
        except BaseException:
            if getattr(capsule, "cleanup_state", None) != "CLOSED":
                capsule.cleanup_state = "QUARANTINED"
                capsule.cleanup_retry_at = max(
                    getattr(capsule, "cleanup_retry_at", 0), time.monotonic() + 30
                )
                capsule.healthy, capsule.busy = False, True
                if cold:
                    self.admission.quarantined.add(getattr(capsule, "identity", None))
            raise
        finally:
            if getattr(capsule, "cleanup_state", None) == "CLOSED":
                if capsule in self.pool:
                    self.pool.remove(capsule)
                self.admission.quarantined.discard(getattr(capsule, "identity", None))
                for key, active in list(self.active.items()):
                    if active is capsule:
                        self.active.pop(key, None)

    def cleanup_complete_for(self, request):
        return self.cleanup_complete_key(self.key(request))

    def cleanup_complete_key(self, key):
        return key not in self.active

    async def _dispose_batch(self, selected):
        remaining_batch = list(selected)
        errors = []
        try:
            for capsule in selected:
                remaining_batch.remove(capsule)
                try:
                    await self._dispose(capsule)
                except Exception as exc:
                    errors.append(exc)
        finally:
            for capsule in remaining_batch:
                if capsule in self.pool and not getattr(capsule, "cleanup_state", None):
                    capsule.busy = False
        if errors:
            raise errors[0]

    def cleanup_snapshot(self):
        selected = [c for c in self.pool if getattr(c, "cleanup_state", None)]
        return dict(
            closing=sum(c.cleanup_state == "CLOSING" for c in selected),
            quarantined=sum(c.cleanup_state == "QUARANTINED" for c in selected),
            oldest_seconds=max(
                (
                    time.monotonic() - (getattr(c, "cleanup_started_at", None) or time.monotonic())
                    for c in selected
                ),
                default=0,
            ),
            owners=[
                dict(
                    identity=c.identity,
                    state=c.cleanup_state,
                    error=getattr(c, "cleanup_error", None),
                    pid=getattr(getattr(c, "process", None), "pid", None),
                )
                for c in selected[:8]
            ],
        )

    async def _reserve(self, thread_id):
        async with self._lock:
            idle = [c for c in self.pool if not c.busy and self.live(c)]
            if thread_id is not None:
                match = next((c for c in idle if c.thread_id == thread_id), None)
                if match is not None:
                    match.busy = True
                    return match
            unbound = next((c for c in idle if c.thread_id is None and c.healthy), None)
            if unbound is not None:
                unbound.busy = True
                return unbound
        return None

    async def _take(self, thread_id: str | None) -> Capsule:
        capsule = await self._reserve(thread_id)
        if capsule is not None:
            return capsule
        async with self.admission.boot():
            capsule = await self._reserve(thread_id)
            if capsule is not None:
                return capsule
            capsule = Capsule(self.root)
            capsule.boot_timeout = self.boot_timeout
            self.pool.append(capsule)
            try:
                await progress("BOOT_ATTEMPT", capsule_id=getattr(capsule, "identity", None))
                await capsule.launch()
            except BaseException as exc:
                await self._dispose(capsule, cold=True)
                capsule.boot_timing = getattr(capsule, "boot_timing", None) or {}
                capsule.boot_timing["run_command_sent"] = False
                exc.startup_receipt = capsule.boot_timing
                await progress(
                    "BOOT_FAILED",
                    capsule_id=getattr(capsule, "identity", None),
                    startup_receipt=capsule.boot_timing,
                )
                raise
            return capsule

    def can_start_warm(self, thread_id):
        return bool(
            thread_id
            and any(c.thread_id == thread_id and not c.busy and self.live(c) for c in self.pool)
        )

    async def warm(self, target: int, gate=None, interval_seconds: float = 2.0) -> None:
        target = getattr(self, "w3_warm_target", target)
        if not self.admission.allow_speculation():
            return
        now = time.monotonic()
        if now < self._warm_next_at:
            return
        idle = sum(not c.busy and c.thread_id is None and self.live(c) for c in self.pool)
        self._warm_next_at = now + interval_seconds
        for _ in range(max(0, target - idle - len(self._warming))):
            task = asyncio.create_task(self._warm_one(gate))
            self._warming.add(task)
            task.add_done_callback(self._warming.discard)

    async def _warm_one(self, gate):
        capsule = None
        try:

            def warm_progress(stage, **values):
                if stage == "BOOT_ATTEMPT" and time.monotonic() < self._warm_failed_until:
                    raise RuntimeError("warm scope cooling down")

            with startup_scope(key="warm", priority=1, progress=warm_progress):
                capsule = await self._take(None)
            self._warm_failures = 0
            capsule.healthy, capsule.busy = True, False
            capsule.idle_at = time.monotonic()
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            if "cooling down" not in str(exc):
                self._warm_failures += 1
                self._warm_failed_until = time.monotonic() + (
                    30 if self._warm_failures == 1 else 60
                )
            self._warm_next_at = self._warm_failed_until
            if capsule is not None:
                await self._dispose(capsule)

    async def prepare_fork(self, request, cwd, parent_thread_id):
        return await self._prepare(request, cwd, parent_thread_id)

    async def prepare_resume(self, request, cwd):
        return await self._prepare(request, cwd, None)

    async def _prepare(self, request, cwd, parent_thread_id):
        capsule = await self._take(None)
        try:
            async with self.admission.handshake:
                await progress(
                    "FORK_DISPATCHED" if parent_thread_id else "RESUMING_CHILD",
                    capsule_id=getattr(capsule, "identity", None),
                )
                await capsule.send(
                    {
                        "type": "prepare_fork" if parent_thread_id else "prepare_resume",
                        "request": request.model_dump(mode="json"),
                        "cwd": str(cwd),
                        "parent_thread_id": parent_thread_id,
                        "timing_directory": str(self.root / "boundary-timing"),
                    }
                )
                ready = await asyncio.wait_for(capsule.receive(), remaining(self.prepare_timeout))
            capsule.thread_id = ready["thread_id"]
            await progress("CHILD_KNOWN", thread_id=capsule.thread_id)
            capsule.prepared = True
            capsule.healthy, capsule.busy = True, False
            capsule.idle_at = time.monotonic()
            return capsule.thread_id
        except BaseException as exc:
            capsule.close_reason = type(exc).__name__
            await self._dispose(capsule)
            exc.startup_receipt = capsule.boot_timing
            raise

    async def release_thread(self, thread_id: str) -> None:
        async with self._lock:
            selected = [c for c in self.pool if c.thread_id == thread_id]
            if any(c.busy for c in selected):
                raise ValueError("cannot release an active thread capsule")
            for capsule in selected:
                capsule.busy = True
        await self._dispose_batch(selected)

    async def start(self, request: WorkerRunRequest, cwd: Path) -> Capsule:
        try:
            with span("capsule.acquire"):
                capsule = await self._take(request.thread_id)
        except BaseException as exc:
            self._failures[self.key(request)] = {
                **getattr(exc, "startup_receipt", {}),
                "run_command_sent": False,
            }
            raise
        capsule.prepared = False
        capsule.ready_slot = False
        self.active[self.key(request)] = capsule
        boot_already_ready = capsule.healthy
        capsule.healthy = False
        capsule.run_command_sent = False
        async with self.admission.handshake:
            capsule.run_command_sent = True
            await capsule.send(
                {
                    "type": "run",
                    "request": request.model_dump(mode="json"),
                    "cwd": str(cwd),
                    "receipt_path": str(receipt_path(self.root, request)),
                }
            )
            while True:
                data = await capsule.receive()
                if data["type"] in {"identity", "started"}:
                    capsule.thread_id, capsule.turn_id = data["thread_id"], data.get("turn_id")
                    if self.receipt_callback is not None:
                        await self.receipt_callback(request, capsule.thread_id, capsule.turn_id)
                if data["type"] == "started":
                    capsule.sdk_deadline = data.get("sdk_deadline")
                    capsule.startup_timing = data.get("startup_timing")
                    if capsule.startup_timing is not None:
                        capsule.startup_timing["boot_already_ready"] = boot_already_ready
                        capsule.startup_timing["boot"] = capsule.boot_timing
                    return capsule

    def failure_for(self, request):
        return self._failures.pop(self.key(request), None)

    async def release(self, request: WorkerRunRequest) -> None:
        capsule = self.active.get(self.key(request))
        if capsule is None:
            return
        # C4 retains its persisted conversation, but must reload attempt-scoped
        # MCP credentials and observation destinations on the next turn.
        shared_c4 = (
            request.research_lane is ResearchLane.GLOBAL_RESEARCH
            and request.agent_role is CodexAgentRole.C4
        )
        if not capsule.healthy or request.max_subagents or shared_c4:
            if not capsule.healthy:
                self._failures[self.key(request)] = {
                    **(capsule.boot_timing or {}),
                    "run_command_sent": capsule.run_command_sent,
                }
            try:
                await self._dispose(capsule)
            finally:
                if capsule not in self.pool:
                    self.active.pop(self.key(request), None)
        else:
            capsule.busy = False
            capsule.idle_at = time.monotonic()
            capsule.handoff_until = (
                capsule.idle_at + self.handoff_seconds
                if request.task_phase == "trade_decision"
                and getattr(capsule, "last_result_status", None) == "completed"
                else 0
            )
        self.active.pop(self.key(request), None)

    async def reap_idle(self, *, all_idle: bool = False, pressure: bool = False) -> int:
        async with self._lock:
            protected = set()
            if pressure and not all_idle:
                profiles = set()
                for c in self.pool:
                    profile = getattr(c, "ready_profile", None)
                    if c.ready_slot and self.live(c) and profile not in profiles:
                        profiles.add(profile)
                        protected.add(c.identity)
            selected = [
                c
                for c in self.pool
                if (
                    getattr(c, "cleanup_state", None) == "QUARANTINED"
                    and time.monotonic() >= getattr(c, "cleanup_retry_at", 0)
                )
                or (
                    not c.busy
                    and (
                        all_idle
                        or not self.live(c)
                        or (
                            pressure
                            and c.identity not in protected
                            and getattr(c, "handoff_until", 0) <= time.monotonic()
                        )
                        or (
                            not pressure
                            and getattr(c, "handoff_until", 0) <= time.monotonic()
                            and c.thread_id is not None
                            and not c.ready_slot
                            and time.monotonic() - c.idle_at >= (300 if c.prepared else 30)
                        )
                    )
                )
            ]
            for capsule in selected:
                capsule.busy = True
        await self._dispose_batch(selected)
        return len(selected)

    async def probe(self) -> dict[str, object]:
        capsule = await self._reserve(None)
        if capsule is None:
            return {"provider_probe": "deferred_capacity"}
        try:
            await capsule.send({"type": "probe"})
            return dict((await asyncio.wait_for(capsule.receive(), 30))["result"])
        except BaseException:
            await self._dispose(capsule)
            raise
        finally:
            if capsule in self.pool and not getattr(capsule, "cleanup_state", None):
                capsule.healthy, capsule.busy = True, False
                capsule.idle_at = time.monotonic()

    async def close(self) -> None:
        for task in list(self._warming):
            task.cancel()
        await asyncio.gather(*list(self._warming), return_exceptions=True)
        await self._dispose_batch(list(self.pool))
