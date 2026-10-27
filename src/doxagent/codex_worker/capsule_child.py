"""Private line protocol. Each process owns one SDK client and one resident thread."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, replace
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

    def boot_marker(stage):
        value = dict(
            type="boot_progress",
            stage=stage,
            pid=os.getpid(),
            wall_ms=(time.perf_counter_ns() - boot_start) / 1e6,
            cpu_ms=time.process_time() * 1000,
        )
        try:
            directory = os.environ.get("DOXAGENT_MCP_BUDGET_ROOT")
            if directory:
                target = Path(directory).parent / "boot.json"
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps(value), encoding="utf-8")
                temporary.replace(target)
        except OSError:
            pass
        wire.write(json.dumps(value) + "\n")
        wire.flush()

    boot_marker("SCHEMA_IMPORT")
    from .schema import WorkerRunRequest

    schema_ready = time.perf_counter_ns()
    boot_marker("SDK_IMPORT")
    from .sdk_runtime import OpenAICodexRuntime

    sdk_ready = time.perf_counter_ns()
    from doxagent.w3_timing import BoundaryTrace

    def send(payload: dict[str, Any]) -> None:
        wire.write(json.dumps(payload, ensure_ascii=False) + "\n")
        wire.flush()

    boot_marker("RUNTIME_CONSTRUCT")
    runtime = OpenAICodexRuntime()
    runtime_ready = time.perf_counter_ns()
    boot_marker("SDK_INITIALIZE")
    await runtime.initialize()
    initialized_at = time.perf_counter_ns()
    boot_marker("SDK_READY")
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
            deadline = getattr(
                handle, "sdk_deadline", time.monotonic() + payload["request"]["timeout_seconds"]
            )
            send(
                {
                    "type": "started",
                    "sdk_deadline": deadline,
                    "thread_id": handle.thread_id,
                    "turn_id": handle.turn_id,
                    "startup_timing": startup,
                }
            )
            from .turn_deadline import run_with_deadline

            async def save_completed_fact(completed):
                if getattr(handle, "sdk_completed_monotonic", float("inf")) > deadline:
                    return
                from doxagent.control_io import read_io

                from .result_receipt import commit

                data = asdict(completed)
                data["telemetry"] = (
                    completed.telemetry.model_dump(mode="json") if completed.telemetry else None
                )
                data["execution_timing"] = dict(
                    sdk_dispatched_at=getattr(handle, "sdk_dispatched_at", None),
                    sdk_completed_at=getattr(handle, "sdk_completed_at", None),
                    sdk_completed_monotonic=handle.sdk_completed_monotonic,
                    sdk_deadline_monotonic=deadline,
                )
                await read_io(commit, Path(payload["receipt_path"]), data)

            handle.sdk_completion_callback = save_completed_fact
            result = await run_with_deadline(handle, deadline)
            result = replace(
                result,
                execution_timing=dict(
                    sdk_dispatched_at=getattr(handle, "sdk_dispatched_at", None),
                    sdk_completed_at=getattr(handle, "sdk_completed_at", None),
                    sdk_deadline_monotonic=deadline,
                    sdk_completed_monotonic=getattr(handle, "sdk_completed_monotonic", None),
                    result_ready_monotonic=time.monotonic(),
                ),
            )
            data = asdict(result)
            data["telemetry"] = (
                result.telemetry.model_dump(mode="json") if result.telemetry else None
            )
            from doxagent.control_io import read_io

            from .result_receipt import commit

            await read_io(commit, Path(payload["receipt_path"]), data)
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
