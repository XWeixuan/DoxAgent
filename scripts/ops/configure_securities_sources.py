"""Prepare the exact approved SECZ portfolio and three COIN additions; preserve activation."""

import argparse
import json
import os
import sqlite3
from pathlib import Path

from doxagent.message_bus_v2.securities_sources import initial_sources, subscription_ids, SOURCES
from doxagent.message_bus_v2.corporate_ir import SOURCE_ID as IR
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.configuration import CandidateConfiguration


def configure(bus, tickers=("SECZ", "COIN")):
    repo = bus.repository
    before = {t: repo.get_ticker_state(t) for t in tickers}
    terms_before = {t: MonitoringTermsService(repo).get(t) for t in tickers}
    out = {}
    with repo.atomic_intake():
        for s in initial_sources():
            current = repo.get_source(s.source_id)
            if current is None:
                bus.register_source(s)
            elif (current.adapter_ref, current.default_parameters) != (
                s.adapter_ref,
                s.default_parameters,
            ):
                raise ValueError("Existing securities source recipe differs: " + s.source_id)
        aggregate = bus.require_source(IR)
        entries = list(aggregate.default_parameters["endpoints"])
        endpoint = next(s for s in initial_endpoints() if s.endpoint_id == "secz.press").model_dump(
            mode="json"
        )
        prior = next((e for e in entries if e["endpoint_id"] == "secz.press"), None)
        if prior and prior != endpoint:
            raise ValueError("Existing SECZ IR differs")
        if prior is None:
            bus.update_source(
                IR,
                {
                    "default_parameters": {
                        **aggregate.default_parameters,
                        "endpoints": [*entries, endpoint],
                    }
                },
                actor=UpdateActor.USER,
                reason="User-approved Securitize channel in existing IR aggregate",
            )
        for t in tickers:
            approved = (
                subscription_ids(repo.list_sources(), t)
                if t == "SECZ"
                else {s.source_id for s in SOURCES.values() if t in s.tickers}
            )
            deferred = []
            for sid in sorted(approved):
                source = bus.require_source(sid)
                params = {"endpoint_ids": ["secz.press"]} if sid == IR else None
                current = repo.get_binding(t + ":" + sid)
                if current is None:
                    # SYSTEM is the normal initialization preconfiguration path.
                    # A live ticker missing Massive terms cannot bypass its gate.
                    found = terms_before[t]
                    from doxagent.message_bus_v2.massive_relevance import massive_configured

                    can_enable = not (
                        source.acquisition_mode.value == "massive_distribution"
                        and before[t] is not None
                        and not massive_configured(
                            found[1] if found else None, source.content_language or "en"
                        )
                    )
                    bus.configure_binding(
                        ticker=t,
                        source_id=sid,
                        source_parameters=params,
                        actor=UpdateActor.SYSTEM,
                        enabled=can_enable,
                        **({"polling": {"target_interval_seconds": 20}} if sid == IR else {}),
                        reason="User-approved SECZ/COIN portfolio; existing initialization owns activation",
                    )
                    if not can_enable:
                        deferred.append(sid)
                elif sid == IR and current.source_parameters != params:
                    bus.update_binding(
                        current.binding_id,
                        {"source_parameters": params},
                        actor=UpdateActor.USER,
                        reason="Select ticker-owned Securitize IR channel",
                    )
            assert repo.get_ticker_state(t) == before[t]
            assert MonitoringTermsService(repo).get(t) == terms_before[t]
            out[t] = {
                "approved": sorted(approved),
                "binding_count": len(repo.list_bindings(ticker=t)),
                "deferred_missing_massive_terms": deferred,
                "state": before[t].status.value if before[t] else "NOT_STARTED",
                "terms_revision": terms_before[t][0] if terms_before[t] else 0,
            }
            if t == "SECZ":
                extra = {b.source_id for b in repo.list_bindings(ticker=t)} - approved
                if extra:
                    raise ValueError(
                        "Unexpected SECZ binding requires review: " + ",".join(sorted(extra))
                    )
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--receipt", type=Path, required=True)
    args = p.parse_args()
    settings = DoxAgentSettings()
    repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path, initialize=False)
    if not args.apply:
        print(
            json.dumps(
                {
                    "new_sources": list(SOURCES),
                    "secz_existing_state": bool(repo.get_ticker_state("SECZ")),
                    "secz_portfolio": sorted(
                        subscription_ids([*repo.list_sources(), *initial_sources()], "SECZ")
                    ),
                }
            )
        )
        return
    os.umask(0o077)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    backup = args.receipt.with_name("before.private.json")
    if not backup.exists():
        backup.write_text(
            json.dumps(
                {
                    "sources": [s.model_dump(mode="json") for s in repo.list_sources()],
                    "bindings": [
                        b.model_dump(mode="json")
                        for t in ["SECZ", "COIN"]
                        for b in repo.list_bindings(ticker=t)
                    ],
                }
            )
        )
    result = {
        "at": utc_now().isoformat(),
        "live": configure(MessageBusV2Service(repo)),
        "candidates": {},
    }
    with sqlite3.connect(
        Path(settings.ticker_initialization_control_path).resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=1,
    ) as d:
        runs = d.execute(
            "SELECT r.id FROM initialization_runs r JOIN ticker_operations o ON o.run_id=r.id WHERE r.ticker='SECZ'"
        ).fetchall()
    for (iid,) in runs:
        c = CandidateConfiguration(repo.path, iid, "SECZ")
        c.prepare()
        result["candidates"][iid] = configure(MessageBusV2Service(c.candidate), ("SECZ",))
    args.receipt.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
