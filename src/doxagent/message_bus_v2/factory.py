"""Construction helpers for the Message Bus v2 process boundary."""

from __future__ import annotations

from dataclasses import dataclass

from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.content import ArticleContentMaterializer
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings


@dataclass
class MessageBusV2Runtime:
    repository: MessageBusV2Repository
    service: MessageBusV2Service
    adapters: AdapterRegistry
    scheduler: GlobalPollScheduler
    crawler_plane: CrawlerPlaneService

    async def close(self) -> None:
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
    )


def build_message_bus_v2_service(
    settings: DoxAgentSettings,
) -> tuple[MessageBusV2Repository, MessageBusV2Service]:
    repository = MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
    materializer = (
        ArticleContentMaterializer() if settings.message_bus_v2_content_enrichment_enabled else None
    )
    service = MessageBusV2Service(repository, materializer=materializer)
    service.bootstrap()
    return repository, service


__all__ = [
    "MessageBusV2Runtime",
    "build_message_bus_v2_runtime",
    "build_message_bus_v2_service",
]
