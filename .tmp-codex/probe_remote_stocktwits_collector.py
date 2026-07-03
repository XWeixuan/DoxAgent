from __future__ import annotations

from doxagent.monitoring.collectors import MonitoringCollectorRegistry
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import TickerSourceBinding
from doxagent.monitoring.service import MonitoringBusService
from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
service = MonitoringBusService(InMemoryMonitoringRepository(), settings=settings)
source = service.repository.get_source("stocktwits_messages")
assert source is not None
binding = service.configure_ticker_source("MU", "stocktwits_messages")
registry = MonitoringCollectorRegistry(settings)
messages = registry.collector_for(source).collect(source=source, binding=binding)
print("collector_force_refresh=" + str(source.config.get("force_refresh")))
print("collector_mode=" + str(source.config.get("mode")))
print("collector_count=" + str(len(messages)))
print("collector_first_id=" + str(messages[0].provider_message_id if messages else None))
print("collector_first_provider=" + str(messages[0].metadata.get("provider") if messages else None))
