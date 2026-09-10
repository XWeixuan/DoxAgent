"""Private line protocol. Each process owns one SDK client and one resident thread."""

from __future__ import annotations

import asyncio
import json
import os
import sys
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
    from .schema import WorkerRunRequest
    from .sdk_runtime import OpenAICodexRuntime

    def send(payload: dict[str, Any]) -> None:
        wire.write(json.dumps(payload, ensure_ascii=False) + "\n")
        wire.flush()

    runtime = OpenAICodexRuntime()
    runtime.receipt_callback = lambda thread, turn: send(
        {"type": "identity", "thread_id": thread, "turn_id": turn}
    )
    handle: Any = None
    task: asyncio.Task[None] | None = None

    async def execute(payload: dict[str, Any]) -> None:
        nonlocal handle
        try:
            handle = await runtime.start(
                WorkerRunRequest.model_validate(payload["request"]), Path(payload["cwd"])
            )
            send({"type": "started", "thread_id": handle.thread_id, "turn_id": handle.turn_id})
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
                    "message": str(exc)[:4000],
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
