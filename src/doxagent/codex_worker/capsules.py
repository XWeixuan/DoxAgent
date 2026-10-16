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

from .process_tree import owned_processes, signal_owned, terminate_owned
from .result_receipt import receipt_path
from .schema import WorkerRunRequest, WorkerTurnTelemetry
from .sdk_runtime import WorkerTurnResult


class CapsuleError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Capsule:
    def __init__(self, root: Path) -> None:
        self.identity = uuid4().hex
        self.root = root
        self.process: asyncio.subprocess.Process | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.busy = True
        self.healthy = False
        self.idle_at = 0.0
        self._log: Any = None
        self._stderr_task: asyncio.Task[None] | None = None
        self.stderr_bytes = 0
        self.startup_timing: dict[str, Any] | None = None
        self.prepared = False

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
        owned = owned_processes(self.identity)
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
        await asyncio.wait_for(self.receive(), 30)

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
                return WorkerTurnResult(**data)

    async def interrupt(self) -> None:
        self.healthy = False
        await self.send({"type": "interrupt"})

    async def close(self) -> None:
        if self.process is not None:
            if self.process.returncode is None:
                try:
                    await self.send({"type": "close"})
                    await asyncio.wait_for(self.process.wait(), 5)
                except (TimeoutError, BrokenPipeError, ConnectionError):
                    pass
            try:
                await terminate_owned(self.process, self.identity)
            except Exception as exc:
                raise CapsuleError(
                    "CAPSULE_CLEANUP_QUARANTINED", "owned process cleanup failed"
                ) from exc
        if self._log is not None:
            self._log.close()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)


class CapsuleRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pool: list[Capsule] = []
        self.active: dict[str, Capsule] = {}
        self._lock = asyncio.Lock()
        self.receipt_callback: Any = None
        self._warming: set[asyncio.Task] = set()
        self._warm_next_at = 0.0

    async def recover(self) -> None:
        import signal

        # The caller already holds the exclusive worker-directory OS lock.
        for owner in (self.root / "capsules").glob("*/owner.json"):
            identity = owner.parent.name
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

    async def _take(self, thread_id: str | None) -> Capsule:
        async with self._lock:
            idle = [c for c in self.pool if not c.busy]
            if thread_id is not None:
                match = next((c for c in idle if c.thread_id == thread_id), None)
                if match is not None:
                    match.busy = True
                    return match
            unbound = next((c for c in idle if c.thread_id is None and c.healthy), None)
            if unbound is not None:
                unbound.busy = True
                return unbound
            capsule = Capsule(self.root)
            self.pool.append(capsule)
        try:
            await capsule.launch()
        except BaseException:
            await capsule.close()
            async with self._lock:
                self.pool.remove(capsule)
            raise
        return capsule

    def can_start_warm(self, thread_id):
        return bool(
            thread_id
            and any(c.thread_id == thread_id and not c.busy and c.healthy for c in self.pool)
        )

    async def warm(self, target: int, gate=None, interval_seconds: float = 2.0) -> None:
        now = time.monotonic()
        if now < self._warm_next_at:
            return
        idle = sum(not c.busy and c.thread_id is None for c in self.pool)
        self._warm_next_at = now + interval_seconds
        for _ in range(max(0, target - idle - len(self._warming))):
            task = asyncio.create_task(self._warm_one(gate))
            self._warming.add(task)
            task.add_done_callback(self._warming.discard)

    async def _warm_one(self, gate):
        capsule = Capsule(self.root)
        self.pool.append(capsule)
        try:
            if gate is not None:
                async with gate:
                    await capsule.launch()
            else:
                await capsule.launch()
            capsule.healthy, capsule.busy = True, False
            capsule.idle_at = time.monotonic()
        except BaseException:
            self._warm_next_at = time.monotonic() + 30
            await capsule.close()
            self.pool.remove(capsule)

    async def prepare_fork(self, request, cwd, parent_thread_id):
        capsule = await self._take(None)
        try:
            await capsule.send(
                {
                    "type": "prepare_fork",
                    "request": request.model_dump(mode="json"),
                    "cwd": str(cwd),
                    "parent_thread_id": parent_thread_id,
                    "timing_directory": str(self.root / "boundary-timing"),
                }
            )
            ready = await asyncio.wait_for(capsule.receive(), 15)
            capsule.thread_id = ready["thread_id"]
            capsule.prepared = True
            capsule.healthy, capsule.busy = True, False
            capsule.idle_at = time.monotonic()
            return capsule.thread_id
        except BaseException:
            await capsule.close()
            self.pool.remove(capsule)
            raise

    async def release_thread(self, thread_id: str) -> None:
        async with self._lock:
            selected = [c for c in self.pool if c.thread_id == thread_id]
            if any(c.busy for c in selected):
                raise ValueError("cannot release an active thread capsule")
            for capsule in selected:
                capsule.busy = True
        for capsule in selected:
            await capsule.close()
            self.pool.remove(capsule)

    async def start(self, request: WorkerRunRequest, cwd: Path) -> Capsule:
        with span("capsule.acquire"):
            capsule = await self._take(request.thread_id)
        capsule.prepared = False
        self.active[self.key(request)] = capsule
        boot_already_ready = capsule.healthy
        capsule.healthy = False
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
                capsule.startup_timing = data.get("startup_timing")
                if capsule.startup_timing is not None:
                    capsule.startup_timing["boot_already_ready"] = boot_already_ready
                return capsule

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
            await capsule.close()
            self.pool.remove(capsule)
        else:
            capsule.busy = False
            capsule.idle_at = time.monotonic()
        self.active.pop(self.key(request), None)

    async def reap_idle(self, *, all_idle: bool = False) -> int:
        async with self._lock:
            selected = [
                c
                for c in self.pool
                if not c.busy
                and (
                    all_idle
                    or (
                        c.thread_id is not None
                        and time.monotonic() - c.idle_at >= (300 if c.prepared else 30)
                    )
                )
            ]
            for capsule in selected:
                capsule.busy = True
        for capsule in selected:
            await capsule.close()
            self.pool.remove(capsule)
        return len(selected)

    async def probe(self) -> dict[str, object]:
        capsule = await self._take(None)
        try:
            await capsule.send({"type": "probe"})
            return dict((await asyncio.wait_for(capsule.receive(), 30))["result"])
        finally:
            await capsule.close()
            self.pool.remove(capsule)

    async def close(self) -> None:
        for task in list(self._warming):
            task.cancel()
        await asyncio.gather(*list(self._warming), return_exceptions=True)
        for capsule in list(self.pool):
            await capsule.close()
            self.pool.remove(capsule)
