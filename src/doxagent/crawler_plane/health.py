"""Crawler-specific health evaluation and persistent alert lifecycle."""

from __future__ import annotations

from doxagent.crawler_plane.repository import CrawlerPlaneRepository
from doxagent.crawler_plane.schema import (
    CrawlerAlert,
    CrawlerAlertPolicy,
    CrawlerAlertType,
    CrawlerExecutionResult,
    CrawlerExecutionStatus,
)


class CrawlerHealthService:
    def __init__(self, repository: CrawlerPlaneRepository) -> None:
        self.repository = repository

    def bootstrap_policies(self, crawler_id: str) -> None:
        defaults = (
            CrawlerAlertPolicy(
                crawler_id=crawler_id,
                alert_type=CrawlerAlertType.EXECUTION_FAILURE,
                window=1,
            ),
            CrawlerAlertPolicy(
                crawler_id=crawler_id,
                alert_type=CrawlerAlertType.DISCOVERY_ANOMALY,
                window=3,
            ),
            CrawlerAlertPolicy(
                crawler_id=crawler_id,
                alert_type=CrawlerAlertType.CONTENT_DRIFT,
                enabled=False,
                threshold=100,
            ),
            CrawlerAlertPolicy(
                crawler_id=crawler_id,
                alert_type=CrawlerAlertType.TRANSPORT_ANOMALY,
                window=1,
            ),
        )
        existing = {value.alert_type for value in self.repository.list_alert_policies(crawler_id)}
        for policy in defaults:
            if policy.alert_type not in existing:
                self.repository.save_alert_policy(policy)

    def evaluate(self, execution: CrawlerExecutionResult) -> None:
        policies = {
            value.alert_type: value
            for value in self.repository.list_alert_policies(execution.crawler_id)
            if value.source_id in {None, execution.source_id}
        }
        self._execution_failure(execution, policies.get(CrawlerAlertType.EXECUTION_FAILURE))
        self._empty_discovery(execution, policies.get(CrawlerAlertType.DISCOVERY_ANOMALY))
        self._content_drift(execution, policies.get(CrawlerAlertType.CONTENT_DRIFT))
        self._transport(execution, policies.get(CrawlerAlertType.TRANSPORT_ANOMALY))

    def _execution_failure(
        self, value: CrawlerExecutionResult, policy: CrawlerAlertPolicy | None
    ) -> None:
        if policy is None or not policy.enabled:
            self._resolve(value, CrawlerAlertType.EXECUTION_FAILURE)
            return
        if value.status is CrawlerExecutionStatus.SUCCEEDED:
            self._resolve(value, CrawlerAlertType.EXECUTION_FAILURE)
            return
        self._open(
            value,
            CrawlerAlertType.EXECUTION_FAILURE,
            value.error_message or "Crawler execution failed.",
            {"error_code": value.error_code, "status": value.status.value},
        )

    def _empty_discovery(
        self, value: CrawlerExecutionResult, policy: CrawlerAlertPolicy | None
    ) -> None:
        if (
            policy is None
            or not policy.enabled
            or value.status is not CrawlerExecutionStatus.SUCCEEDED
        ):
            if policy is None or not policy.enabled:
                self._resolve(value, CrawlerAlertType.DISCOVERY_ANOMALY)
            return
        recent = self.repository.list_executions(
            crawler_id=value.crawler_id, binding_id=value.binding_id, limit=policy.window
        )
        if len(recent) >= policy.window and all(not item.observations for item in recent):
            self._open(
                value,
                CrawlerAlertType.DISCOVERY_ANOMALY,
                f"Crawler returned no observations for {policy.window} consecutive executions.",
                {"window": policy.window},
            )
        else:
            self._resolve(value, CrawlerAlertType.DISCOVERY_ANOMALY)

    def _content_drift(
        self, value: CrawlerExecutionResult, policy: CrawlerAlertPolicy | None
    ) -> None:
        if policy is None or not policy.enabled or policy.threshold is None:
            self._resolve(value, CrawlerAlertType.CONTENT_DRIFT)
            return
        short = [item for item in value.observations if len(item.body) < policy.threshold]
        if short:
            self._open(
                value,
                CrawlerAlertType.CONTENT_DRIFT,
                f"{len(short)} observation(s) are shorter than {policy.threshold} characters.",
                {"threshold": policy.threshold, "short_count": len(short)},
            )
        else:
            self._resolve(value, CrawlerAlertType.CONTENT_DRIFT)

    def _transport(self, value: CrawlerExecutionResult, policy: CrawlerAlertPolicy | None) -> None:
        if policy is None or not policy.enabled:
            self._resolve(value, CrawlerAlertType.TRANSPORT_ANOMALY)
            return
        failures = int(value.diagnostics.get("transport_failure_count", 0))
        if failures:
            self._open(
                value,
                CrawlerAlertType.TRANSPORT_ANOMALY,
                f"Crawler execution observed {failures} transport failure(s).",
                {"transport_failure_count": failures},
            )
        else:
            self._resolve(value, CrawlerAlertType.TRANSPORT_ANOMALY)

    def _open(
        self,
        execution: CrawlerExecutionResult,
        alert_type: CrawlerAlertType,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        self.repository.upsert_alert(
            CrawlerAlert(
                alert_key=f"{alert_type.value}:{execution.crawler_id}:{execution.binding_id}",
                crawler_id=execution.crawler_id,
                source_id=execution.source_id,
                binding_id=execution.binding_id,
                execution_id=execution.execution_id,
                alert_type=alert_type,
                message=message,
                metadata=metadata,
            )
        )

    def _resolve(
        self,
        execution: CrawlerExecutionResult,
        alert_type: CrawlerAlertType,
    ) -> None:
        key = f"{alert_type.value}:{execution.crawler_id}:{execution.binding_id}"
        for alert in self.repository.list_alerts(crawler_id=execution.crawler_id, open_only=True):
            if alert.alert_key == key:
                self.repository.resolve_alert(alert.alert_id)
                return


__all__ = ["CrawlerHealthService"]
