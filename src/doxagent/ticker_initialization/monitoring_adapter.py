"""Deterministic default configuration preparation without O4 research."""

from .configuration import CandidateConfiguration
from .schema import NodeResult


class MonitoringConfigurationAdapter:
    def __init__(self, settings):
        self.settings = settings

    async def reconcile(self, context):
        return await self.execute(context)

    async def execute(self, context):
        candidate = CandidateConfiguration(
            self.settings.message_bus_v2_sqlite_path,
            context.run.initialization_id,
            context.run.ticker,
        )
        path = candidate.prepare()
        usable = [
            b
            for b in candidate.candidate.list_bindings(ticker=context.run.ticker)
            if b.enabled
            and b.tombstoned_at is None
            and (s := candidate.candidate.get_source(b.source_id))
            and s.enabled
        ]
        if not usable:
            raise ValueError("ticker has no usable monitoring binding")
        return NodeResult(
            artifacts={
                "monitoring_configuration": {
                    "initialization_id": context.run.initialization_id,
                    "database": str(path),
                    "enabled_binding_count": len(usable),
                }
            }
        )
