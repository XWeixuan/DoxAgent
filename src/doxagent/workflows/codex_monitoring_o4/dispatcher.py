"""Alert-to-O4 repair request projection with deliberately narrow trigger scope."""

from __future__ import annotations

from doxagent.crawler_plane.schema import CrawlerAlertStatus, CrawlerAlertType
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.service import MessageBusV2Service

from .orchestrator import MonitoringO4Orchestrator
from .schema import O4Request, RepairTrigger

_CRAWLER_REPAIR_ALERTS = {
    CrawlerAlertType.EXECUTION_FAILURE,
    CrawlerAlertType.DISCOVERY_ANOMALY,
    CrawlerAlertType.CONTENT_DRIFT,
    CrawlerAlertType.TRANSPORT_ANOMALY,
}


class O4AlertDispatcher:
    def __init__(
        self,
        *,
        orchestrator: MonitoringO4Orchestrator,
        message_bus: MessageBusV2Service,
        crawler_plane: CrawlerPlaneService,
    ) -> None:
        self.orchestrator = orchestrator
        self.message_bus = message_bus
        self.crawler_plane = crawler_plane

    def scan(self) -> list[O4Request]:
        queued: list[O4Request] = []
        for alert in self.message_bus.repository.list_alerts(active_only=True):
            # Aggregate/capacity/acquisition/oversized alerts are intentionally not O4 triggers.
            if alert.code != "source_poll_failure" or not alert.binding_id or not alert.source_id:
                continue
            ticker = alert.binding_id.split(":", 1)[0].upper()
            queued.append(
                self.orchestrator.submit_repair(
                    ticker=ticker,
                    trigger=RepairTrigger(
                        trigger_type="message_bus",
                        alert_id=alert.alert_id,
                        alert_code=alert.code,
                        repeat_count=alert.repeat_count,
                        source_id=alert.source_id,
                        binding_id=alert.binding_id,
                        metadata=alert.metadata,
                    ),
                )
            )
        for alert in self.crawler_plane.list_alerts(open_only=True):
            if (
                alert.status is not CrawlerAlertStatus.OPEN
                or alert.alert_type not in _CRAWLER_REPAIR_ALERTS
            ):
                continue
            execution = (
                self.crawler_plane.get_execution(alert.execution_id)
                if alert.execution_id
                else None
            )
            binding_id = alert.binding_id or (execution.binding_id if execution else None)
            source_id = alert.source_id or (execution.source_id if execution else None)
            if not binding_id or not source_id:
                continue
            package = self.crawler_plane.get_crawler(alert.crawler_id)
            ticker = execution.ticker if execution else binding_id.split(":", 1)[0].upper()
            queued.append(
                self.orchestrator.submit_repair(
                    ticker=ticker,
                    trigger=RepairTrigger(
                        trigger_type="crawler",
                        alert_id=alert.alert_id,
                        alert_code=alert.alert_type.value,
                        repeat_count=alert.repeat_count,
                        source_id=source_id,
                        binding_id=binding_id,
                        crawler_id=alert.crawler_id,
                        execution_id=alert.execution_id,
                        metadata={
                            **alert.metadata,
                            "active_version": package.active_version,
                        },
                    ),
                )
            )
        return queued


__all__ = ["O4AlertDispatcher"]
