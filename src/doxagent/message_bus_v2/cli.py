"""Independent Message Bus v2 worker CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal

from doxagent.message_bus_v2.factory import build_message_bus_v2_runtime
from doxagent.settings import DoxAgentSettings


async def _run_worker(settings: DoxAgentSettings, *, once: bool) -> int:
    if not settings.message_bus_v2_enabled:
        if once:
            return 0
        stop = _worker_stop_event()
        print("Message Bus v2 is disabled; worker is idle.")
        await stop.wait()
        return 0
    runtime = build_message_bus_v2_runtime(settings)
    try:
        if once:
            results = await runtime.scheduler.run_once()
            print(json.dumps([item.model_dump(mode="json") for item in results]))
            return 0
        stop = _worker_stop_event()
        await runtime.scheduler.run_forever(
            loop_sleep_seconds=settings.message_bus_v2_worker_sleep_seconds,
            stop=stop,
        )
        return 0
    finally:
        await runtime.close()


def _worker_stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        resolved = getattr(signal, name, None)
        if resolved is not None:
            try:
                loop.add_signal_handler(resolved, stop.set)
            except NotImplementedError:
                pass
    return stop


def main() -> None:
    parser = argparse.ArgumentParser(description="DoxAgent Message Bus v2 worker")
    parser.add_argument("command", choices=("run-worker", "run-once", "status"))
    args = parser.parse_args()
    settings = DoxAgentSettings()
    if args.command == "status":
        if not settings.message_bus_v2_enabled:
            print(
                json.dumps(
                    {
                        "enabled": False,
                        "sqlite_path": settings.message_bus_v2_sqlite_path,
                        "counts": {},
                    }
                )
            )
            return
        runtime = build_message_bus_v2_runtime(settings)
        try:
            print(
                json.dumps(
                    {
                        "enabled": settings.message_bus_v2_enabled,
                        "sqlite_path": settings.message_bus_v2_sqlite_path,
                        "counts": runtime.repository.snapshot_counts(),
                    }
                )
            )
        finally:
            asyncio.run(runtime.close())
        return
    raise SystemExit(asyncio.run(_run_worker(settings, once=args.command == "run-once")))


if __name__ == "__main__":
    main()
