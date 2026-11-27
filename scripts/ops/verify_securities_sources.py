"""Read-only portfolio/source acceptance with no credentials or monitoring words."""

import json
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.securities_sources import SOURCES, subscription_ids
from doxagent.message_bus_v2.schema import utc_now


def main():
    repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
    sources = repo.list_sources()
    approved = subscription_ids(sources, "SECZ")
    bs = repo.list_bindings(ticker="SECZ")
    assert {b.source_id for b in bs} == approved
    assert all(
        b.enabled
        for b in bs
        if b.source_id
        not in {s.source_id for s in sources if s.acquisition_mode.value == "massive_distribution"}
    )
    own = repo.get_binding("SECZ:corporate_ir_press_releases")
    assert own.source_parameters == {"endpoint_ids": ["secz.press"]}
    assert own.polling.target_interval_seconds == 20
    for sid, spec in SOURCES.items():
        s = repo.get_source(sid)
        assert s.enabled and s.default_polling_config.target_interval_seconds == 20
        bindings = repo.list_bindings(source_id=sid)
        assert {b.ticker for b in bindings} == set(spec.tickers)
        assert all(b.enabled and b.polling.enabled for b in bindings)
    result = {
        "at": utc_now().isoformat(),
        "secz_sources": sorted(approved),
        "secz_binding_count": len(bs),
        "new_sources": list(SOURCES),
        "new_source_cadence_seconds": 20,
        "ir_endpoint": "secz.press",
        "coin_additions": [sid for sid, s in SOURCES.items() if "COIN" in s.tickers],
        "disabled_secz_bindings": [
            b.source_id for b in bs if not b.enabled or not b.polling.enabled
        ],
    }
    for t in ["SECZ", "COIN"]:
        state = repo.get_ticker_state(t)
        terms = MonitoringTermsService(repo).get(t)
        result[t] = {
            "state": state.status.value if state else "NOT_STARTED",
            "terms_revision": terms[0] if terms else 0,
        }
    print(json.dumps(result, indent=2))
    repo.close()


if __name__ == "__main__":
    main()
