"""Bounded, thread-affine SDK process capsules, independently reclaimable."""

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
                    await asyncio.wait_for(self.process.wait(), 10)
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
    def __init__(self, root: Path, capacity: int = 2) -> None:
        self.root, self.capacity = root, capacity
        self.pool: list[Capsule] = []
        self.active: dict[str, Capsule] = {}
        self._lock = asyncio.Lock()
        self.receipt_callback: Any = None

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

    async def _take(self, thread_id: str | None, weight: int = 1) -> Capsule:
        async with self._lock:
            idle = [c for c in self.pool if not c.busy]
            if thread_id is not None and weight == 1:
                match = next((c for c in idle if c.thread_id == thread_id), None)
                if match is not None:
                    match.busy = True
                    return match
            # Clear other resident sessions before allocating native subagent capacity.
            while len(self.pool) > self.capacity - weight and idle:
                old = idle.pop(0)
                if weight == 1 and old.thread_id == thread_id:
                    old.busy = True
                    return old
                await old.close()
                self.pool.remove(old)
            if len(self.pool) >= self.capacity - weight + 1:
                raise CapsuleError("RESOURCE_CAPACITY", "all capsules occupied")
            capsule = Capsule(self.root)
            self.pool.append(capsule)
            try:
                await capsule.launch()
            except BaseException:
                await capsule.close()
                self.pool.remove(capsule)
                raise
            return capsule

    async def start(self, request: WorkerRunRequest, cwd: Path) -> Capsule:
        capsule = await self._take(request.thread_id, 1 + request.max_subagents)
        self.active[self.key(request)] = capsule
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
                return capsule

    async def release(self, request: WorkerRunRequest) -> None:
        capsule = self.active.get(self.key(request))
        if capsule is None:
            return
        if not capsule.healthy or request.max_subagents:
            await capsule.close()
            self.pool.remove(capsule)
        else:
            capsule.busy = False
            capsule.idle_at = time.monotonic()
        self.active.pop(self.key(request), None)

    async def reap_idle(self, *, all_idle: bool = False) -> int:
        reclaimed = 0
        async with self._lock:
            for capsule in list(self.pool):
                if not capsule.busy and (all_idle or time.monotonic() - capsule.idle_at >= 30):
                    await capsule.close()
                    self.pool.remove(capsule)
                    reclaimed += 1
        return reclaimed

    async def probe(self) -> dict[str, object]:
        capsule = await self._take(None)
        try:
            await capsule.send({"type": "probe"})
            return dict((await asyncio.wait_for(capsule.receive(), 30))["result"])
        finally:
            await capsule.close()
            self.pool.remove(capsule)

    async def close(self) -> None:
        for capsule in list(self.pool):
            await capsule.close()
            self.pool.remove(capsule)
