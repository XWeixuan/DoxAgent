"""Construction helpers shared by the Site Access service and its clients."""

from __future__ import annotations

from doxagent.settings import DoxAgentSettings

from .repository import SiteStrategyRepository
from .seeds import bootstrap_seed
from .service import SiteStrategyService


def build_site_strategy_service(settings: DoxAgentSettings) -> SiteStrategyService:
    repository = SiteStrategyRepository(settings.site_access_sqlite_path)
    service = SiteStrategyService(
        repository,
        profile_root=settings.site_access_profile_root,
        browser_headless=settings.site_access_browser_headless,
        browser_channel=settings.site_access_browser_channel,
        browser_max_processes=settings.site_access_browser_max_processes,
        browser_max_pages=settings.site_access_browser_max_pages,
        browser_idle_seconds=settings.site_access_browser_idle_seconds,
        safety_path=settings.safety_state_path,
        supervisor_socket=settings.site_access_chrome_supervisor_socket,
        controller_id=settings.site_access_controller_id,
    )
    bootstrap_seed(repository, service)
    return service


__all__ = ["build_site_strategy_service"]
