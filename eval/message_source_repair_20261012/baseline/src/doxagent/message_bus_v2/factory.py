"""Construction helpers for the Message Bus v2 process boundary."""

from __future__ import annotations

from dataclasses import dataclass

from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.distribution import DistributionWorker
from doxagent.message_bus_v2.jev import JevClient
from doxagent.message_bus_v2.news_policy import HiddenNewsIngressPolicy
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.repository import SiteStrategyRepository


@dataclass
class MessageBusV2Runtime:
    repository: MessageBusV2Repository
    service: MessageBusV2Service
    adapters: AdapterRegistry
    scheduler: GlobalPollScheduler
    crawler_plane: CrawlerPlaneService
    jev: JevClient | None = None

    async def close(self) -> None:
        await self.scheduler.close()
        if self.jev is not None:
            await self.jev.close()
        await self.adapters.close()
        await self.crawler_plane.close()
        self.repository.close()


def build_message_bus_v2_runtime(settings: DoxAgentSettings) -> MessageBusV2Runtime:
    repository, service = build_message_bus_v2_service(settings)
    crawler_plane = build_crawler_plane_service(settings, message_bus=service)
    adapters = AdapterRegistry(
        settings,
        adapter_root=settings.message_bus_v2_adapter_root,
        crawler_plane=crawler_plane,
    )
    scheduler = GlobalPollScheduler(repository, service, adapters)
    secret = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else ""
    jev = JevClient(secret, timeout=settings.message_bus_jev_timeout_seconds) if (
        settings.message_bus_jev_enabled and secret
    ) else None
    scheduler.distribution_worker = DistributionWorker(
        scheduler.distribution, service, scheduler.terms, jev=jev,
    )
    if settings.ticker_initialization_control_path:
        from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
        from doxagent.persistent_runtime_v2.journal import RuntimeJournal

        scheduler.runtime_orchestration = BusOrchestration(
            RuntimeJournal(settings.persistent_runtime_v2_sqlite_path)
        )
    if settings.ticker_initialization_control_path:
        from doxagent.ticker_initialization.consumers import admit_bus_revisions
        from doxagent.ticker_initialization.repository import InitializationRepository

        control = InitializationRepository(settings.ticker_initialization_control_path)
        scheduler.activation_admission = lambda: admit_bus_revisions(control, service)
        scheduler.initialization_control = control
    return MessageBusV2Runtime(
        repository=repository,
        service=service,
        adapters=adapters,
        scheduler=scheduler,
        crawler_plane=crawler_plane,
        jev=jev,
    )


def build_message_bus_v2_service(
    settings: DoxAgentSettings,
) -> tuple[MessageBusV2Repository, MessageBusV2Service]:
    def site_language(site_id: str) -> str | None:
        registry = SiteStrategyRepository(settings.site_access_sqlite_path)
        try:
            strategy = registry.get_strategy(site_id)
            return strategy.default_content_language if strategy else None
        finally:
            registry.close()

    repository = MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
    service = MessageBusV2Service(
        repository,
        enrichment_queue_enabled=(
            settings.content_enrichment_enabled
            and settings.message_bus_v2_content_enrichment_enabled
        ),
        enrichment_retry_deadline_seconds=settings.content_enrichment_retry_deadline_seconds,
        enrichment_pipeline_version=(
            ("body_v2.2" if settings.site_access_enabled else "body_v2.1")
            if settings.content_enrichment_pipeline_enabled
            else None
        ),
        hidden_news_policy=HiddenNewsIngressPolicy.from_strings(
            domains=settings.message_bus_v2_hidden_news_domains,
            publishers=settings.message_bus_v2_hidden_news_publishers,
        ),
        site_language_resolver=site_language,
    )
    service.bootstrap()
    return repository, service


__all__ = [
    "MessageBusV2Runtime",
    "build_message_bus_v2_runtime",
    "build_message_bus_v2_service",
]
