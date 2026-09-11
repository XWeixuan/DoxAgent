"""Independent worker entry point for the global V2 content-enrichment hub."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.settings import DoxAgentSettings


def _stop_event() -> asyncio.Event:
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


async def _run(settings: DoxAgentSettings, *, once: bool) -> int:
    repository, bus = build_message_bus_v2_service(settings)
    hub = ContentEnrichmentHub(
        repository,
        bus,
        concurrency=settings.content_enrichment_max_concurrency,
        retry_delay_seconds=settings.content_enrichment_retry_delay_seconds,
    )
    try:
        if once:
            processed = await hub.run_once()
            print(json.dumps({"processed_count": processed}))
            return 0
        if not (
            settings.message_bus_v2_enabled
            and settings.content_enrichment_enabled
            and settings.message_bus_v2_content_enrichment_enabled
        ):
            print("Content enrichment is disabled; worker is idle.")
            await _stop_event().wait()
            return 0
        stop = _stop_event()
        while not stop.is_set():
            processed = await hub.run_once()
            if processed == 0:
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.content_enrichment_worker_sleep_seconds
                    )
                except TimeoutError:
                    pass
        return 0
    finally:
        await hub.close()
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DoxAgent V2 content-enrichment worker")
    parser.add_argument("command", choices=("run-worker", "run-once", "status"))
    args = parser.parse_args()
    settings = DoxAgentSettings()
    if args.command == "status":
        repository, _ = build_message_bus_v2_service(settings)
        try:
            print(
                json.dumps(
                    {
                        "enabled": settings.content_enrichment_enabled,
                        "concurrency": settings.content_enrichment_max_concurrency,
                        "queued": len(repository.list_enrichment_jobs(limit=100_000)),
                    }
                )
            )
        finally:
            repository.close()
        return
    raise SystemExit(asyncio.run(_run(settings, once=args.command == "run-once")))


if __name__ == "__main__":
    main()
