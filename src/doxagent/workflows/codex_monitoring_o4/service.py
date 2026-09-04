"""Construction boundary for the complete O4 monitoring workflow."""

from __future__ import annotations

from dataclasses import dataclass

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings

from .dispatcher import O4AlertDispatcher
from .orchestrator import MonitoringO4Orchestrator, O4ConfigurationContextProvider
from .policy import O4MutationPolicy
from .repository import MonitoringO4Repository
from .runner import MonitoringO4AgentRunner


@dataclass
class MonitoringO4Runtime:
    repository: MonitoringO4Repository
    orchestrator: MonitoringO4Orchestrator
    runner: MonitoringO4AgentRunner
    worker: HttpCodexWorkerClient
    message_bus_repository: MessageBusV2Repository | None
    message_bus: MessageBusV2Service | None
    crawler_plane: CrawlerPlaneService | None
    dispatcher: O4AlertDispatcher | None

    async def close(self) -> None:
        await self.worker.aclose()
        if self.crawler_plane is not None:
            await self.crawler_plane.close()
        if self.message_bus_repository is not None:
            self.message_bus_repository.close()
        self.repository.close()


def build_monitoring_o4_runtime(
    settings: DoxAgentSettings,
    *,
    context_provider: O4ConfigurationContextProvider | None = None,
) -> MonitoringO4Runtime:
    if not settings.codex_monitoring_o4_enabled:
        raise RuntimeError("DOXAGENT_CODEX_MONITORING_O4_ENABLED is false")
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise ValueError("O4 requires Codex worker bearer token and capability secret")
    repository = MonitoringO4Repository(settings.codex_monitoring_o4_sqlite_path)
    worker = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
    )
    message_bus_repository: MessageBusV2Repository | None = None
    message_bus: MessageBusV2Service | None = None
    crawler_plane: CrawlerPlaneService | None = None
    dispatcher: O4AlertDispatcher | None = None
    if settings.message_bus_v2_enabled:
        message_bus_repository, message_bus = build_message_bus_v2_service(settings)
        crawler_plane = build_crawler_plane_service(settings, message_bus=message_bus)
    runner = MonitoringO4AgentRunner(
        worker=worker,
        workspace=worker,
        repository=repository,
        model=settings.codex_monitoring_o4_model,
        model_provider=settings.codex_model_provider,
        timeout_seconds=settings.codex_monitoring_o4_timeout_seconds,
    )
    orchestrator = MonitoringO4Orchestrator(
        repository=repository,
        runner=runner,
        message_bus=message_bus,
        message_bus_enabled=settings.message_bus_v2_enabled,
        context_provider=context_provider,
        mutation_policy=O4MutationPolicy(
            standard_poll_seconds=settings.o4_standard_poll_seconds,
            tikhub_poll_seconds=settings.o4_tikhub_poll_seconds,
            alert_after_seconds=settings.o4_alert_after_seconds,
        ),
    )
    if message_bus is not None and crawler_plane is not None:
        dispatcher = O4AlertDispatcher(
            orchestrator=orchestrator,
            message_bus=message_bus,
            crawler_plane=crawler_plane,
        )
    return MonitoringO4Runtime(
        repository=repository,
        orchestrator=orchestrator,
        runner=runner,
        worker=worker,
        message_bus_repository=message_bus_repository,
        message_bus=message_bus,
        crawler_plane=crawler_plane,
        dispatcher=dispatcher,
    )


__all__ = ["MonitoringO4Runtime", "build_monitoring_o4_runtime"]
