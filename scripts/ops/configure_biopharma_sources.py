"""Prepare the user-approved five-ticker portfolio through existing owner services."""

import argparse
import json
import os
import sqlite3
from pathlib import Path

from doxagent.message_bus_v2.biopharma_sources import TICKERS, initial_sources, subscription_ids
from doxagent.message_bus_v2.corporate_ir import SOURCE_ID as IR
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.configuration import CandidateConfiguration


def configure(bus, tickers=TICKERS):
    repo = bus.repository
    before = {t: repo.get_ticker_state(t) for t in tickers}
    result = {}
    with repo.atomic_intake():
        for source in initial_sources():
            current = repo.get_source(source.source_id)
            if current is None:
                bus.register_source(source)
            elif current.model_dump(
                include={"adapter_ref", "default_parameters", "activation_window"}
            ) != source.model_dump(
                include={"adapter_ref", "default_parameters", "activation_window"}
            ):
                raise ValueError("Existing biopharma recipe differs: " + source.source_id)
        source = bus.require_source(IR)
        entries = list(source.default_parameters["endpoints"])
        present = {e["endpoint_id"] for e in entries}
        entries.extend(
            s.model_dump(mode="json")
            for s in initial_endpoints()
            if s.owner_ticker in TICKERS and s.endpoint_id not in present
        )
        if entries != source.default_parameters["endpoints"]:
            bus.update_source(
                IR,
                {"default_parameters": {**source.default_parameters, "endpoints": entries}},
                actor=UpdateActor.USER,
                reason="Append five approved biopharma IR channels",
            )
        for ticker in tickers:
            approved = subscription_ids(repo.list_sources(), ticker)
            for sid in sorted(approved):
                parameters = {"endpoint_ids": [ticker.lower() + ".press"]} if sid == IR else {}
                current = repo.get_binding(ticker + ":" + sid)
                if current is None:
                    bus.configure_binding(
                        ticker=ticker,
                        source_id=sid,
                        source_parameters=parameters,
                        actor=UpdateActor.SYSTEM,
                        reason="User-approved biopharma preconfiguration; normal initialization owns activation",
                    )
                elif (
                    not current.enabled
                    or not current.polling.enabled
                    or (sid == IR and current.source_parameters != parameters)
                ):
                    patch = {
                        "enabled": True,
                        "polling": {**current.polling.model_dump(), "enabled": True},
                    }
                    if sid == IR:
                        patch["source_parameters"] = parameters
                    bus.update_binding(
                        current.binding_id,
                        patch,
                        actor=UpdateActor.USER,
                        reason="Apply user-approved biopharma source portfolio",
                    )
            assert repo.get_ticker_state(ticker) == before[ticker]
            result[ticker] = {
                "approved": sorted(approved),
                "binding_count": len(approved),
                "monitoring_state": before[ticker].status.value
                if before[ticker]
                else "NOT_STARTED",
                "extra_enabled": [
                    b.source_id
                    for b in repo.list_bindings(ticker=ticker)
                    if b.enabled and b.source_id not in approved
                ],
            }
            if result[ticker]["extra_enabled"]:
                raise ValueError("Unexpected enabled bindings require review: " + ticker)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--receipt", type=Path, required=True)
    a = p.parse_args()
    settings = DoxAgentSettings()
    repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path, initialize=False)
    if not a.apply:
        print(
            json.dumps(
                {
                    t: sorted(subscription_ids([*repo.list_sources(), *initial_sources()], t))
                    for t in TICKERS
                }
            )
        )
        return
    os.umask(0o077)
    a.receipt.parent.mkdir(parents=True, exist_ok=True)
    backup = a.receipt.with_name("before.private.json")
    if not backup.exists():
        backup.write_text(
            json.dumps(
                {
                    "sources": [s.model_dump(mode="json") for s in repo.list_sources()],
                    "bindings": [
                        b.model_dump(mode="json")
                        for t in TICKERS
                        for b in repo.list_bindings(ticker=t)
                    ],
                }
            )
        )
    receipt = {
        "at": utc_now().isoformat(),
        "live": configure(MessageBusV2Service(repo)),
        "candidates": {},
    }
    path = settings.ticker_initialization_control_path
    if path and Path(path).is_file():
        with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            # Only an in-progress initialization's current candidate; never touch
            # an archived activation or launch an initialization on the user's behalf.
            runs = db.execute(
                "SELECT r.id,r.ticker FROM initialization_runs r JOIN ticker_operations o ON o.run_id=r.id "
                "WHERE r.ticker IN (?,?,?,?,?)",
                TICKERS,
            ).fetchall()
        for iid, ticker in runs:
            candidate = CandidateConfiguration(repo.path, iid, ticker)
            candidate.prepare()
            receipt["candidates"][iid] = configure(
                MessageBusV2Service(candidate.candidate), (ticker,)
            )
    a.receipt.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
