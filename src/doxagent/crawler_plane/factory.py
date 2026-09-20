"""Construction helpers for the shared Crawler Plane process boundary."""

from __future__ import annotations

from doxagent.crawler_plane.assets import CrawlerAssetStore
from doxagent.crawler_plane.certification import CrawlerCertificationService
from doxagent.crawler_plane.repository import CrawlerPlaneRepository
from doxagent.crawler_plane.runtime import CrawlerWorkerPool, PlaywrightBrowserRuntime
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient, SiteManagedBrowser
from doxagent.site_strategy.tokens import read_token


def build_crawler_plane_service(
    settings: DoxAgentSettings,
    *,
    message_bus: MessageBusV2Service | None = None,
) -> CrawlerPlaneService:
    """Build one application service shared by O4 tools and human APIs."""

    repository = CrawlerPlaneRepository(settings.crawler_plane_sqlite_path)
    assets = CrawlerAssetStore(settings.crawler_plane_root)
    worker_pool = CrawlerWorkerPool(process_count=settings.crawler_plane_worker_processes)
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
    service = CrawlerPlaneService(
        repository,
        assets,
        worker_pool=worker_pool,
        browser=(
            SiteManagedBrowser(site_client)
            if site_client is not None
            else PlaywrightBrowserRuntime(
                headless=settings.crawler_plane_browser_headless,
                channel=settings.crawler_plane_browser_channel,
                identity_dir=settings.crawler_plane_browser_identity_dir,
                cdp_url=settings.crawler_plane_browser_cdp_url,
                proxy_url=settings.crawler_egress_proxy_url,
            )
        ),
        execution_timeout_seconds=settings.crawler_plane_execution_timeout_seconds,
        max_response_bytes=settings.crawler_plane_max_response_bytes,
        message_bus=message_bus,
        site_access_client=site_client,
    )
    service.attach_certification_service(CrawlerCertificationService(service, repository))
    return service


__all__ = ["build_crawler_plane_service"]
