"""Restart-safe O4 queue and alert worker."""

from __future__ import annotations

import asyncio
import signal

from doxagent.settings import DoxAgentSettings

from .service import build_monitoring_o4_runtime


async def serve(settings: DoxAgentSettings | None = None) -> None:
    resolved = settings or DoxAgentSettings()
    runtime = build_monitoring_o4_runtime(resolved)
    runtime.repository.recover_interrupted_requests()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, name, None)
        if signal_value is not None:
            try:
                loop.add_signal_handler(signal_value, stopped.set)
            except NotImplementedError:
                pass
    try:
        while not stopped.is_set():
            if runtime.dispatcher is not None:
                runtime.dispatcher.scan()
            result = await runtime.orchestrator.process_next()
            if result is None:
                try:
                    await asyncio.wait_for(
                        stopped.wait(), timeout=resolved.codex_monitoring_o4_worker_sleep_seconds
                    )
                except TimeoutError:
                    pass
    finally:
        await runtime.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
