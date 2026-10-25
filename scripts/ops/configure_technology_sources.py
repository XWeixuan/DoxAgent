"""Configure the requested technology subscriptions; reserve IONQ until it is registered."""

import argparse
import json
import sqlite3
from pathlib import Path

from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.schema import UpdateActor, binding_id_for
from doxagent.message_bus_v2.technology_sources import CATALOG, initial_sources
from doxagent.message_bus_v2.thefly_source import SOURCE_ID as FLY_SOURCE_ID
from doxagent.message_bus_v2.thefly_source import initial_source as initial_fly_source


def configure(service, *, include_fly=False):
    repo = service.repository
    report = {"sources": [], "bindings": [], "initial_terms": [], "reserved": []}
    seeds = json.loads(
        Path(__file__)
        .with_name("technology-monitoring-terms-20261026.json")
        .read_text(encoding="utf-8")
    )
    with repo.atomic_intake():
        active = {state.ticker for state in repo.list_ticker_states()}
        requested = {ticker for spec in CATALOG for ticker in spec.tickers} & active
        terms_service = MonitoringTermsService(repo)
        for ticker in sorted(requested):
            if terms_service.get(ticker) is None:
                if ticker not in seeds:
                    raise ValueError("existing monitored ticker has no monitoring terms: " + ticker)
                revision = terms_service.apply(
                    TickerMonitoringTerms.model_validate(seeds[ticker]),
                    actor="technology-source-onboarding",
                )
                report["initial_terms"].append({"ticker": ticker, "revision": revision})
        for seed in [*initial_sources(), *([initial_fly_source()] if include_fly else [])]:
            existing = repo.get_source(seed.source_id)
            if existing is None:
                service.register_source(seed)
                action = "created"
            elif existing.adapter_ref != seed.adapter_ref or existing.entry_url != seed.entry_url:
                raise ValueError("existing source differs from verified recipe: " + seed.source_id)
            elif not existing.enabled:
                service.update_source(
                    seed.source_id,
                    {"enabled": True},
                    actor=UpdateActor.USER,
                    reason="Requested technology sources",
                )
                action = "enabled"
            else:
                action = "unchanged"
            report["sources"].append({"source_id": seed.source_id, "action": action})
        for spec in CATALOG:
            for ticker in spec.tickers:
                key = binding_id_for(ticker, spec.source_id)
                if ticker not in active:
                    report["reserved"].append({"ticker": ticker, "source_id": spec.source_id})
                    continue
                current = terms_service.get(ticker)[1]
                current.validate_languages({spec.language})
                existing = repo.get_binding(key, include_tombstoned=True)
                if existing is None or existing.tombstoned_at:
                    service.configure_binding(
                        ticker=ticker,
                        source_id=spec.source_id,
                        actor=UpdateActor.USER,
                        reason="Requested technology sources",
                    )
                    action = "created"
                elif not existing.enabled or not existing.polling.enabled:
                    service.update_binding(
                        key,
                        {
                            "enabled": True,
                            "polling": {
                                **existing.polling.model_dump(mode="json"),
                                "enabled": True,
                            },
                        },
                        actor=UpdateActor.USER,
                        reason="Requested technology sources",
                    )
                    action = "enabled"
                else:
                    action = "unchanged"
                report["bindings"].append({"binding_id": key, "action": action})
        if include_fly:
            for ticker in sorted(active):
                key = binding_id_for(ticker, FLY_SOURCE_ID)
                existing = repo.get_binding(key, include_tombstoned=True)
                if existing is None or existing.tombstoned_at:
                    service.configure_binding(
                        ticker=ticker,
                        source_id=FLY_SOURCE_ID,
                        actor=UpdateActor.USER,
                        reason="Requested The Fly ticker news; public full articles only",
                    )
                    action = "created"
                elif not existing.enabled or not existing.polling.enabled:
                    service.update_binding(
                        key,
                        {
                            "enabled": True,
                            "polling": {
                                **existing.polling.model_dump(mode="json"),
                                "enabled": True,
                            },
                        },
                        actor=UpdateActor.USER,
                        reason="Requested The Fly ticker news",
                    )
                    action = "enabled"
                else:
                    action = "unchanged"
                report["bindings"].append({"binding_id": key, "action": action})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--include-fly", action="store_true")
    args = parser.parse_args()
    if args.apply:
        from doxagent.message_bus_v2.repository import MessageBusV2Repository
        from doxagent.message_bus_v2.service import MessageBusV2Service

        repo = MessageBusV2Repository(args.bus)
        try:
            result = configure(
                MessageBusV2Service(repo, enrichment_queue_enabled=True),
                include_fly=args.include_fly,
            )
        finally:
            repo.close()
    else:
        with sqlite3.connect(Path(args.bus).resolve().as_uri() + "?mode=ro", uri=True) as db:
            tickers = {row[0] for row in db.execute("SELECT ticker FROM ticker_monitoring_states")}
            existing = {
                row[0]
                for row in db.execute(
                    "SELECT binding_id FROM ticker_source_bindings WHERE tombstoned_at IS NULL"
                )
            }
        result = {
            "sources": [spec.source_id for spec in CATALOG],
            "bindings": [
                {
                    "binding_id": binding_id_for(ticker, spec.source_id),
                    "exists": binding_id_for(ticker, spec.source_id) in existing,
                    "reserved": ticker not in tickers,
                }
                for spec in CATALOG
                for ticker in spec.tickers
            ],
        }
        if args.include_fly:
            result["sources"].append(FLY_SOURCE_ID)
            result["bindings"].extend(
                {
                    "binding_id": binding_id_for(ticker, FLY_SOURCE_ID),
                    "exists": binding_id_for(ticker, FLY_SOURCE_ID) in existing,
                    "reserved": False,
                }
                for ticker in sorted(tickers)
            )
    Path(args.receipt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.receipt).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
