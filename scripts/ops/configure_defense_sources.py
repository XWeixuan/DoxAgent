"""Configure only the requested RKLB/ONDS subscriptions; default is a read-only preview."""

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from doxagent.message_bus_v2.defense_sources import CATALOG, initial_sources
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.schema import UpdateActor, binding_id_for


def hebrew_terms(current, revision):
    value = current.model_dump(mode="json")
    value["expected_revision"] = revision
    aliases = {
        "ondas": "אונדס",
        "airobotics": "איירובוטיקס",
        "american robotics": "אמריקן רובוטיקס",
    }
    for concept in value["l1_concepts"]:
        english = concept["expressions"]["en"]
        concept["expressions"].setdefault("he", aliases.get(english.casefold(), english))
    if "he" not in value["l2"]:
        value["l2"]["he"] = copy.deepcopy(value["l2"]["en"])
        value["l2"]["he"]["groups"].append(
            {
                "id": "defense-hebrew-company-aliases",
                "any": [{"literal": term} for term in aliases.values()],
            }
        )
    return TickerMonitoringTerms.model_validate(value)


def configure(service):
    repo = service.repository
    report = {"applied": True, "sources": [], "bindings": [], "terms": None, "initial_terms": []}
    with repo.atomic_intake():
        for ticker in ("RKLB", "ONDS"):
            if repo.get_ticker_state(ticker) is None:
                raise ValueError("ticker not registered:" + ticker)
        terms_service = MonitoringTermsService(repo)
        seeds = json.loads(
            Path(__file__)
            .with_name("defense-monitoring-terms-20261024.json")
            .read_text(encoding="utf-8")
        )
        for ticker in ("RKLB", "ONDS"):
            if terms_service.get(ticker) is None:
                revision = terms_service.apply(
                    TickerMonitoringTerms.model_validate(seeds[ticker]),
                    actor="defense-source-onboarding",
                )
                report["initial_terms"].append({"ticker": ticker, "revision": revision})
        revision, current = terms_service.get("ONDS")
        desired = hebrew_terms(current, revision)
        old = current.model_dump(mode="json")
        new = desired.model_dump(mode="json")
        old.pop("expected_revision", None)
        new.pop("expected_revision", None)
        if old != new:
            report["terms"] = {
                "ticker": "ONDS",
                "revision": terms_service.apply(desired, actor="defense-source-onboarding"),
            }
        for seed in initial_sources():
            existing = repo.get_source(seed.source_id)
            if existing is None:
                service.register_source(seed)
                report["sources"].append({"source_id": seed.source_id, "action": "created"})
            elif existing.adapter_ref != seed.adapter_ref or existing.entry_url != seed.entry_url:
                raise ValueError("existing source differs from verified recipe:" + seed.source_id)
            elif not existing.enabled:
                service.update_source(
                    seed.source_id,
                    {"enabled": True},
                    actor=UpdateActor.USER,
                    reason="Requested defense/space sources",
                )
                report["sources"].append({"source_id": seed.source_id, "action": "enabled"})
        for e in CATALOG:
            for ticker in e.tickers:
                key = binding_id_for(ticker, e.source_id)
                existing = repo.get_binding(key)
                if existing is None:
                    service.configure_binding(
                        ticker=ticker,
                        source_id=e.source_id,
                        actor=UpdateActor.USER,
                        reason="Requested defense/space sources",
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
                        reason="Requested defense/space sources",
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
            known = {r[0] for r in db.execute("SELECT source_id FROM source_definitions")}
            bindings = {r[0] for r in db.execute("SELECT binding_id FROM ticker_source_bindings")}
            result = {
                "applied": False,
                "sources": [
                    {
                        "source_id": e.source_id,
                        "exists": e.source_id in known,
                        "scheduler_group": s.scheduler_group,
                    }
                    for e, s in zip(CATALOG, initial_sources(), strict=True)
                ],
                "bindings": [
                    {
                        "binding_id": binding_id_for(ticker, e.source_id),
                        "exists": binding_id_for(ticker, e.source_id) in bindings,
                    }
                    for e in CATALOG
                    for ticker in e.tickers
                ],
            }
    destination = Path(args.receipt)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
