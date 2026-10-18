"""Private line protocol. Each process owns one SDK client and one resident thread."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


async def main() -> None:
    if os.name == "posix":
        import ctypes

        # Detached grandchildren remain under this capsule until it is reaped.
        ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)
    wire = sys.stdout
    sys.stdout = sys.stderr
    boot_start = time.perf_counter_ns()
    from .schema import WorkerRunRequest

    schema_ready = time.perf_counter_ns()
    from .sdk_runtime import OpenAICodexRuntime

    sdk_ready = time.perf_counter_ns()
    from doxagent.w3_timing import BoundaryTrace

    def send(payload: dict[str, Any]) -> None:
        wire.write(json.dumps(payload, ensure_ascii=False) + "\n")
        wire.flush()

    runtime = OpenAICodexRuntime()
    runtime_ready = time.perf_counter_ns()
    await runtime.initialize()
    initialized_at = time.perf_counter_ns()
    send(
        {
            "type": "boot_ready",
            "state": "SDK_READY",
            "timing": {
                "process_ready_ms": (runtime_ready - boot_start) / 1e6,
                "sdk_initialize_ms": (initialized_at - runtime_ready) / 1e6,
                "sdk_ready_ms": (initialized_at - boot_start) / 1e6,
            },
        }
    )
    used = False
    runtime.receipt_callback = lambda thread, turn: send(
        {"type": "identity", "thread_id": thread, "turn_id": turn}
    )
    handle: Any = None
    task: asyncio.Task[None] | None = None

    async def execute(payload: dict[str, Any]) -> None:
        nonlocal handle, used
        try:
            trace = BoundaryTrace(plane="capsule_start")
            with trace.activate():
                handle = await runtime.start(
                    WorkerRunRequest.model_validate(payload["request"]), Path(payload["cwd"])
                )
            startup = dict(
                reused=used,
                schema_import_ms=(schema_ready - boot_start) / 1e6,
                sdk_import_ms=(sdk_ready - schema_ready) / 1e6,
                runtime_construct_ms=(runtime_ready - sdk_ready) / 1e6,
                sdk_start=trace.snapshot(),
            )
            used = True
            send(
                {
                    "type": "started",
                    "thread_id": handle.thread_id,
                    "turn_id": handle.turn_id,
                    "startup_timing": startup,
                }
            )
            result = await handle.run()
            data = asdict(result)
            data["telemetry"] = (
                result.telemetry.model_dump(mode="json") if result.telemetry else None
            )
            from .result_receipt import commit

            commit(Path(payload["receipt_path"]), data)
            send({"type": "result", "result": data})
        except Exception as exc:
            send(
                {
                    "type": "error",
                    "code": str(getattr(exc, "code", "CODEX_WORKER_ERROR")),
                    "message": (str(exc) or type(exc).__name__)[:4000],
                }
            )
        finally:
            handle = None

    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            payload = json.loads(line)
            if payload["type"] == "run":
                if task is not None and not task.done():
                    raise RuntimeError("capsule already executing")
                task = asyncio.create_task(execute(payload))
            elif payload["type"] in {"prepare_fork", "prepare_resume"}:
                try:
                    from doxagent.w3_timing import save_trace

                    trace = BoundaryTrace(
                        plane="capsule_prepare", run_id=payload["request"]["run_id"]
                    )
                    try:
                        with trace.activate():
                            request = WorkerRunRequest.model_validate(payload["request"])
                            if payload["type"] == "prepare_fork":
                                thread_id = await runtime.prepare_fork(
                                    request,
                                    Path(payload["cwd"]),
                                    parent_thread_id=payload["parent_thread_id"],
                                )
                            else:
                                thread_id = await runtime.prepare_resume(
                                    request, Path(payload["cwd"])
                                )
                    finally:
                        save_trace(trace, payload.get("timing_directory"))
                    from .workspace_store import LocalWorkspaceStore

                    if payload["type"] == "prepare_fork":
                        LocalWorkspaceStore._atomic_write(
                            Path(payload["cwd"]) / ".w3-fork-receipt.json",
                            json.dumps(
                                {
                                    "thread_id": thread_id,
                                    "parent_id": payload["parent_thread_id"],
                                    "run_id": payload["request"]["run_id"],
                                }
                            ).encode(),
                        )
                    send({"type": "ready", "thread_id": thread_id})
                except Exception as exc:
                    send(
                        {
                            "type": "error",
                            "code": getattr(exc, "code", type(exc).__name__),
                            "message": str(exc) or type(exc).__name__,
                        }
                    )
            elif payload["type"] == "interrupt" and handle is not None:
                await asyncio.wait_for(handle.interrupt(), 5)
            elif payload["type"] == "probe":
                send({"type": "probe", "result": await runtime.probe()})
            elif payload["type"] == "close":
                break
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
