from __future__ import annotations

import httpx

from doxagent.monitoring.collectors import MonitoringCollectorRegistry
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.service import MonitoringBusService
from doxagent.settings import DoxAgentSettings


def fp(value: str | None) -> str:
    if value and len(value) > 8:
        return value[:4] + "..." + value[-4:]
    return "<set>" if value else "<empty>"

settings = DoxAgentSettings()
print("container_STOCKTWITS_RAPIDAPI_KEY=" + fp(settings.stocktwits_rapidapi_key))
print("container_STOCKTWITS_RAPIDAPI_FALLBACK_KEY=" + fp(settings.stocktwits_rapidapi_fallback_key))
assert settings.stocktwits_rapidapi_key
assert settings.stocktwits_rapidapi_fallback_key

requests: list[httpx.Request] = []

def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    if request.headers["x-rapidapi-key"] == "primary-key":
        return httpx.Response(429, json={"message": "quota exceeded"}, request=request)
    return httpx.Response(
        200,
        json={"messages": [{"id": 909, "created_at": "2026-06-23T10:05:00Z"}]},
        request=request,
    )

registry = MonitoringCollectorRegistry(
    DoxAgentSettings(
        monitoring_storage_mode="memory",
        stocktwits_rapidapi_key="primary-key",
        stocktwits_rapidapi_fallback_key="fallback-key",
        stocktwits_max_retries=3,
    ),
    client=httpx.Client(transport=httpx.MockTransport(handler)),
)
service = MonitoringBusService(InMemoryMonitoringRepository(), collectors=registry)
binding = service.configure_ticker_source("MU", "stocktwits_messages")
source = service.repository.get_source("stocktwits_messages")
assert source is not None
messages = registry.collector_for(source).collect(source=source, binding=binding)
assert messages[0].provider_message_id == "909"
sequence = [request.headers["x-rapidapi-key"] for request in requests]
assert sequence == ["primary-key", "primary-key", "primary-key", "fallback-key"], sequence
print("fallback_sequence_ok=" + ",".join(sequence))
