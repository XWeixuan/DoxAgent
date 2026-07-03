from __future__ import annotations

import json
from doxagent.monitoring.service import MonitoringBusService
from doxagent.monitoring.stocktwits_durable import is_stocktwits_durable_source
from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
svc = MonitoringBusService.from_settings(settings)
source = svc.repository.get_source("stocktwits_messages")
print("settings_stocktwits_storage_mode=" + settings.stocktwits_storage_mode)
print("settings_stocktwits_sqlite_path=" + settings.stocktwits_sqlite_path)
if source is None:
    print("source=None")
else:
    print("source_enabled=" + str(source.enabled))
    print("source_endpoint_kind=" + source.endpoint_kind.value)
    print("source_is_durable=" + str(is_stocktwits_durable_source(source)))
    print("source_config=" + json.dumps(source.config, ensure_ascii=False, sort_keys=True))
for ticker in ["MU", "AMD", "AAPL"]:
    binding = svc.repository.get_binding(ticker, "stocktwits_messages")
    print("binding_" + ticker + "=" + (json.dumps(binding.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) if binding else "None"))
try:
    states = svc.repository.list_poll_states(ticker="MU")
    print("poll_states_MU=" + json.dumps([s.model_dump(mode="json") for s in states], ensure_ascii=False, sort_keys=True)[:4000])
except Exception as exc:
    print("poll_states_error=" + type(exc).__name__ + ": " + str(exc)[:500])
