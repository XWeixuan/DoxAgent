"""Read-only configuration acceptance; emit no credentials or monitoring words."""

import json

from doxagent.message_bus_v2.biopharma_sources import TICKERS, SOURCES, subscription_ids
from doxagent.message_bus_v2.corporate_ir import SOURCE_ID as IR
from doxagent.message_bus_v2.corporate_ir.schema import IrEndpointSpec
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import utc_now
from doxagent.message_bus_v2.x_official import ACCOUNTS
from doxagent.settings import DoxAgentSettings


def main():
    repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
    now = utc_now()
    result = {"at": now.isoformat(), "tickers": {}, "windows": {}}
    all_sources = repo.list_sources()
    for t in TICKERS:
        approved = subscription_ids(all_sources, t)
        bindings = repo.list_bindings(ticker=t)
        assert {b.source_id for b in bindings} == approved, t
        assert all(b.enabled and b.polling.enabled for b in bindings), t
        own = repo.get_binding(t + ":" + IR)
        assert own.source_parameters["endpoint_ids"] == [t.lower() + ".press"]
        terms = MonitoringTermsService(repo).get(t)
        state = repo.get_ticker_state(t)
        result["tickers"][t] = {
            "sources": sorted(approved),
            "binding_count": len(bindings),
            "state": state.status.value if state else "NOT_STARTED",
            "terms_revision": terms[0] if terms else 0,
            "ir_seconds": own.polling.target_interval_seconds,
            "official_x_default": ACCOUNTS[t],
        }
    for sid, spec in SOURCES.items():
        assert all(b.ticker in spec.tickers for b in repo.list_bindings(source_id=sid)), sid
        source = repo.get_source(sid)
        assert source.default_polling_config.target_interval_seconds == 20, sid
    for source in all_sources:
        if source.activation_window:
            result["windows"][source.source_id] = {
                **source.activation_window.model_dump(mode="json"),
                "active_now": source.active_at(now),
            }
    assert set(result["windows"]) == {"ash_cullinan", "aes_abstract_search"}
    ir = repo.get_source(IR)
    specs = [IrEndpointSpec.model_validate(e) for e in ir.default_parameters["endpoints"]]
    result["ir_endpoint_count"] = len(specs)
    result["new_ir"] = [
        {
            "id": s.endpoint_id,
            "ticker": s.owner_ticker,
            "seconds": s.target_interval_seconds,
            "parser": s.parser_ref,
        }
        for s in specs
        if s.owner_ticker in TICKERS
    ]
    assert len(result["new_ir"]) == 5
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
