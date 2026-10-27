"""Construction helpers for the Message Bus v2 process boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.distribution import DistributionWorker
from doxagent.message_bus_v2.jev import BailianDecisionClient
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
    jev: BailianDecisionClient | None = None

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
    scheduler.collection_plane_enabled = settings.message_bus_v2_collection_plane_enabled
    if scheduler.collection_plane_enabled:
        from .collection_plane.bridge import CaptureBridge
        scheduler.capture_bridge = CaptureBridge(service)
    secret = settings.dashscope_api_key or ""
    jev = BailianDecisionClient(
        secret,
        url=settings.message_bus_decision_url,
        timeout=settings.message_bus_jev_timeout_seconds,
    ) if (
        settings.message_bus_jev_enabled and secret
    ) else None
    scheduler.distribution_worker = DistributionWorker(
        scheduler.distribution, service, scheduler.terms, jev=jev,
    )
    if settings.ticker_initialization_control_path:
        from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
        from doxagent.persistent_runtime_v2.journal import RuntimeJournal

        scheduler.runtime_orchestration = BusOrchestration(
            # Runtime owns this schema. Opening an already provisioned journal
            # must not rerun DDL or wait for its writer just to start collection.
            RuntimeJournal(settings.persistent_runtime_v2_sqlite_path,
                initialize=not Path(settings.persistent_runtime_v2_sqlite_path).is_file(),
                busy_timeout_seconds=5)
        )
    if settings.ticker_initialization_control_path:
        from doxagent.ticker_initialization.consumers import admit_bus_revisions
        from doxagent.ticker_initialization.repository import InitializationRepository

        control = InitializationRepository(settings.ticker_initialization_control_path,
            initialize=not Path(settings.ticker_initialization_control_path).is_file())
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
        if not Path(settings.site_access_sqlite_path).is_file():
            return None
        registry = SiteStrategyRepository(settings.site_access_sqlite_path, read_only=True)
        try:
            strategy = registry.get_strategy(site_id)
            return strategy.default_content_language if strategy else None
        finally:
            registry.close()

    repository = MessageBusV2Repository(settings.message_bus_v2_sqlite_path,
        terminal_receipts_enabled=settings.message_bus_enrichment_terminal_receipts_enabled)
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
    if repository.initialize or not settings.message_bus_v2_collection_plane_enabled:
        service.bootstrap()
    return repository, service


__all__ = [
    "MessageBusV2Runtime",
    "build_message_bus_v2_runtime",
    "build_message_bus_v2_service",
]
