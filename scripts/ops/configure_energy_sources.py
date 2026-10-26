"""Apply requested energy/quantum subscriptions without starting unregistered tickers."""

import argparse
import json
import sqlite3
from pathlib import Path

from doxagent.message_bus_v2.energy_catalog import CATALOG, initial_sources
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.schema import UpdateActor, binding_id_for


def configure(service):
    repo = service.repository
    seeds = json.loads(
        Path(__file__)
        .with_name("energy-monitoring-terms-20261027.json")
        .read_text(encoding="utf-8")
    )
    report = {"sources": [], "bindings": [], "reserved": [], "initial_terms": []}
    with repo.atomic_intake():
        registered = {state.ticker for state in repo.list_ticker_states()}
        terms = MonitoringTermsService(repo)
        requested = {ticker for spec in CATALOG for ticker in spec.tickers} & registered
        for ticker in sorted(requested):
            found = terms.get(ticker)
            if found is None:
                if ticker not in seeds:
                    raise ValueError("existing ticker has no monitoring terms:" + ticker)
                revision = terms.apply(
                    TickerMonitoringTerms.model_validate(seeds[ticker]),
                    actor="energy-source-onboarding",
                )
                report["initial_terms"].append({"ticker": ticker, "revision": revision})
            terms.get(ticker)[1].validate_languages({"en"})
        for source in initial_sources():
            existing = repo.get_source(source.source_id)
            if existing is None:
                service.register_source(source)
                action = "created"
            elif (
                existing.adapter_ref != source.adapter_ref or existing.entry_url != source.entry_url
            ):
                raise ValueError("existing source differs from verified recipe:" + source.source_id)
            elif not existing.enabled:
                service.update_source(
                    source.source_id,
                    {"enabled": True},
                    actor=UpdateActor.USER,
                    reason="Requested energy/government sources",
                )
                action = "enabled"
            else:
                action = "unchanged"
            report["sources"].append({"source_id": source.source_id, "action": action})
        for spec in CATALOG:
            for ticker in spec.tickers:
                if ticker not in registered:
                    report["reserved"].append({"ticker": ticker, "source_id": spec.source_id})
                    continue
                key = binding_id_for(ticker, spec.source_id)
                current = repo.get_binding(key)
                if current is None:
                    service.configure_binding(
                        ticker=ticker,
                        source_id=spec.source_id,
                        actor=UpdateActor.USER,
                        reason="Requested energy/government sources",
                    )
                    action = "created"
                elif not current.enabled or not current.polling.enabled:
                    service.update_binding(
                        key,
                        {
                            "enabled": True,
                            "polling": {**current.polling.model_dump(), "enabled": True},
                        },
                        actor=UpdateActor.USER,
                        reason="Requested energy/government sources",
                    )
                    action = "enabled"
                else:
                    action = "unchanged"
                report["bindings"].append(
                    {"binding_id": key, "action": action, "endpoints": list(spec.urls_for(ticker))}
                )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        from doxagent.message_bus_v2.repository import MessageBusV2Repository
        from doxagent.message_bus_v2.service import MessageBusV2Service

        repo = MessageBusV2Repository(args.bus)
        try:
            result = configure(MessageBusV2Service(repo, enrichment_queue_enabled=True))
        finally:
            repo.close()
    else:
        with sqlite3.connect(Path(args.bus).resolve().as_uri() + "?mode=ro", uri=True) as db:
            tickers = {r[0] for r in db.execute("SELECT ticker FROM ticker_monitoring_states")}
        result = {
            "apply": False,
            "sources": [s.source_id for s in CATALOG],
            "bindings": [
                {
                    "ticker": ticker,
                    "source_id": s.source_id,
                    "reserved": ticker not in tickers,
                    "endpoints": list(s.urls_for(ticker)),
                }
                for s in CATALOG
                for ticker in s.tickers
            ],
        }
    Path(args.receipt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.receipt).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
