"""Dashboard-facing model usage cost aggregation."""

from __future__ import annotations

from datetime import UTC, datetime

from doxagent.model_usage.pricing import ModelPricingCatalog
from doxagent.model_usage.repository import (
    ModelUsageRepository,
    model_usage_repository_from_settings,
)
from doxagent.model_usage.schema import JsonObject, ModelUsageEvent
from doxagent.settings import DoxAgentSettings


class ModelUsageCostService:
    """Convert persisted usage events into Dashboard cost audit records."""

    def __init__(
        self,
        repository: ModelUsageRepository,
        *,
        pricing_catalog: ModelPricingCatalog,
    ) -> None:
        self.repository = repository
        self.pricing_catalog = pricing_catalog

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> ModelUsageCostService:
        resolved = settings or DoxAgentSettings()
        return cls(
            model_usage_repository_from_settings(resolved),
            pricing_catalog=ModelPricingCatalog.from_settings(resolved),
        )

    def cost_records(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = True,
    ) -> list[JsonObject]:
        events = self.repository.list_events(
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            node=node,
            model=model,
            status=status,
            limit=limit,
            offset=offset,
            newest_first=newest_first,
        )
        return [self._record_from_event(event) for event in events]

    def count_records(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
    ) -> int:
        return self.repository.count_events(
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            node=node,
            model=model,
            status=status,
        )

    def has_records(self, *, ticker: str | None = None) -> bool:
        return self.count_records(ticker=ticker) > 0

    def total_cost_usd(
        self,
        *,
        ticker: str | None = None,
        start_time: datetime,
        end_time: datetime,
    ) -> float | None:
        records = self.cost_records(
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            limit=None,
        )
        costs = [
            float(record["cost_usd"])
            for record in records
            if isinstance(record.get("cost_usd"), int | float)
        ]
        if not costs:
            return None
        return round(sum(costs), 6)

    def _record_from_event(self, event: ModelUsageEvent) -> JsonObject:
        pricing = self.pricing_catalog.price(event)
        cost_usd = round(pricing.cost_usd, 6) if pricing is not None else None
        cost_cny = round(pricing.cost_cny, 6) if pricing is not None else None
        pricing_status = "priced" if pricing is not None else "missing_price"
        node = _node_from_event(event)
        return {
            "cost_record_id": event.event_id,
            "time": _dt(event.created_at),
            "ticker": event.ticker or "UNKNOWN",
            "node": node,
            "model": event.model,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "total_tokens": event.total_tokens,
            "cost_usd": cost_usd,
            "is_retry": event.retry_count > 0,
            "status": event.status,
            "pricing_status": pricing_status,
            "source_ref": {
                "provider": event.provider,
                "run_id": event.run_id,
                "source_message_id": event.source_message_id,
                "execution_id": event.execution_id,
                "workflow_node": event.workflow_node,
                "runtime_node": event.runtime_node,
                "agent_name": event.agent_name,
                "task_type": event.task_type,
                "retry_count": event.retry_count,
                "fallback_used": event.fallback_used,
                "error_code": event.error_code,
                "cost_cny": cost_cny,
                "pricing_status": pricing_status,
                "pricing_version": pricing.pricing_version if pricing is not None else None,
                "pricing_source": pricing.pricing_source if pricing is not None else None,
                "discount_rate": pricing.discount_rate if pricing is not None else None,
                "cny_usd_rate": pricing.cny_usd_rate if pricing is not None else None,
            },
        }


def _node_from_event(event: ModelUsageEvent) -> str:
    for value in (
        event.runtime_node,
        event.workflow_node,
        event.agent_name,
        event.task_type,
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
