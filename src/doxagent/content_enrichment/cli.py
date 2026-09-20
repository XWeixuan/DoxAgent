"""Independent worker entry point for the global V2 content-enrichment hub."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from pathlib import Path

from doxagent.content_enrichment.browser import PublisherBrowser
from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token


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
    site_token = read_token(
        settings.site_access_worker_token,
        settings.site_access_worker_token_file,
    )
    if settings.site_access_enabled and not site_token:
        raise ValueError("Site Access is enabled but the worker token is unavailable")
    site_client = (
        SiteAccessClient(
            settings.site_access_url,
            token=site_token,
        )
        if settings.site_access_enabled
        else None
    )
    hub = ContentEnrichmentHub(
        repository,
        bus,
        concurrency=settings.content_enrichment_max_concurrency,
        retry_delay_seconds=settings.content_enrichment_retry_delay_seconds,
        site_access_client=site_client,
        extractor=SharedContentExtractor(
            concurrency=settings.content_enrichment_max_concurrency,
            pipeline_enabled=settings.content_enrichment_pipeline_enabled,
            proxy_url=settings.crawler_egress_proxy_url,
            site_access_client=site_client,
            close_site_access_client=True,
            browser_enabled=settings.content_enrichment_browser_enabled,
            browser_headless=settings.content_enrichment_browser_headless,
            browser_channel=settings.content_enrichment_browser_channel,
            browser_cdp_url=settings.content_enrichment_browser_cdp_url,
            trusted_proxy_dns=settings.content_enrichment_trusted_proxy_dns,
            identity_dir=Path(settings.content_enrichment_identity_dir)
            if settings.content_enrichment_identity_dir
            else None,
            authenticated_hosts={
                h.strip()
                for h in settings.content_enrichment_authenticated_hosts.split(",")
                if h.strip()
            },
            disabled_hosts={
                h.strip()
                for h in settings.content_enrichment_disabled_hosts.split(",")
                if h.strip()
            },
        ),
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
        await hub.run(stop, sleep_seconds=settings.content_enrichment_worker_sleep_seconds)
        return 0
    finally:
        await hub.close()
        repository.close()


async def _login(settings: DoxAgentSettings, host: str, url: str) -> None:
    if not settings.content_enrichment_identity_dir:
        raise ValueError("DOXAGENT_CONTENT_ENRICHMENT_IDENTITY_DIR is required")
    browser = PublisherBrowser(
        identity_dir=Path(settings.content_enrichment_identity_dir),
        authenticated_hosts={
            h.strip()
            for h in settings.content_enrichment_authenticated_hosts.split(",")
            if h.strip()
        },
        headless=False,
        channel=settings.content_enrichment_browser_channel,
        cdp_url=settings.content_enrichment_browser_cdp_url,
        trusted_proxy_dns=settings.content_enrichment_trusted_proxy_dns,
    )
    try:
        await browser.login(host, url)
    finally:
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DoxAgent V2 content-enrichment worker")
    parser.add_argument("command", choices=("run-worker", "run-once", "status", "login"))
    parser.add_argument("--host")
    parser.add_argument("--url")
    args = parser.parse_args()
    settings = DoxAgentSettings()
    if args.command == "login":
        if not args.host or not args.url:
            parser.error("login requires --host and --url; stop the identity worker first")
        asyncio.run(_login(settings, args.host, args.url))
        return
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
