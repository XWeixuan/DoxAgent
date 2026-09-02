"""Construction helpers for the shared Crawler Plane process boundary."""

from __future__ import annotations

from doxagent.crawler_plane.assets import CrawlerAssetStore
from doxagent.crawler_plane.certification import CrawlerCertificationService
from doxagent.crawler_plane.reference import bootstrap_reference_working_copies
from doxagent.crawler_plane.repository import CrawlerPlaneRepository
from doxagent.crawler_plane.runtime import CrawlerWorkerPool
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings


def build_crawler_plane_service(
    settings: DoxAgentSettings,
    *,
    message_bus: MessageBusV2Service | None = None,
    bootstrap_references: bool = True,
) -> CrawlerPlaneService:
    """Build one application service shared by O4 tools and human APIs."""

    repository = CrawlerPlaneRepository(settings.crawler_plane_sqlite_path)
    assets = CrawlerAssetStore(settings.crawler_plane_root)
    worker_pool = CrawlerWorkerPool(process_count=settings.crawler_plane_worker_processes)
    service = CrawlerPlaneService(
        repository,
        assets,
        worker_pool=worker_pool,
        execution_timeout_seconds=settings.crawler_plane_execution_timeout_seconds,
        max_response_bytes=settings.crawler_plane_max_response_bytes,
        message_bus=message_bus,
    )
    service.attach_certification_service(CrawlerCertificationService(service, repository))
    if bootstrap_references:
        bootstrap_reference_working_copies(service)
    return service


__all__ = ["build_crawler_plane_service"]
