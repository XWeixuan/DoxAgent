import pytest

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import SourceDefinition, TickerSourceBinding
from doxagent.message_bus_v2.service import MessageBusV2Service
from scripts.ops.message_efficiency_config_20261127 import RETIRED, configure


def test_config_applies_exact_scope_and_retired_sources_do_not_return(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    service = MessageBusV2Service(repo)
    service.bootstrap()
    for sid in RETIRED:
        source = SourceDefinition(source_id=sid, display_name=sid, adapter_ref="builtin:newswire_rss",
                                  kind="api", acquisition_mode="by_ticker", scheduler_group=sid)
        repo.save_source(source)  # Represent the existing production record before retirement.
    targets = {"dcd_infrastructure_rss": ["NBIS", "BE", "MU"], "dcd_energy_ticker": ["BE", "CEG"],
               "google_news_search_rss": ["IONQ", "INTC", "MU"], "reuters_site_search": ["ONDS", "NBIS"],
               **{sid: ["SNOW"] for sid in RETIRED}}
    for sid, tickers in targets.items():
        for ticker in tickers:
            repo.save_binding(TickerSourceBinding(binding_id=f"{ticker}:{sid}", source_id=sid, ticker=ticker))
    before = [s.model_dump() for s in repo.list_sources()]
    preview = configure(service)
    assert not preview["applied"] and before == [s.model_dump() for s in repo.list_sources()]
    result = configure(service, apply=True, backup=tmp_path / "backup.json")
    assert len(result["unsubscribed"]) == 5
    assert {b.ticker for b in repo.list_bindings(source_id="dcd_infrastructure_rss")} == {"NBIS", "BE"}
    assert {b.ticker for b in repo.list_bindings(source_id="dcd_energy_ticker")} == {"BE"}
    assert {b.ticker for b in repo.list_bindings(source_id="reuters_site_search")} == {"NBIS"}
    google = repo.list_bindings(source_id="google_news_search_rss")
    assert len(google) == 1 and google[0].ticker == "IONQ" and google[0].source_parameters["require_relevance"] is True
    assert repo.get_source("quantum_zeitgeist").entry_url.endswith("/quantum-computing-news/feed/")
    service.bootstrap()
    assert not (set(RETIRED) & {s.source_id for s in repo.list_sources()})
    for sid in RETIRED:
        assert not repo.list_bindings(source_id=sid)
        with pytest.raises(ValueError, match="retired"):
            service.register_source(SourceDefinition(source_id=sid, display_name=sid, adapter_ref="builtin:newswire_rss",
                                                     kind="api", acquisition_mode="by_ticker", scheduler_group=sid))
    assert not configure(service, apply=True, backup=tmp_path / "backup2.json")["unsubscribed"]
    repo.close()
