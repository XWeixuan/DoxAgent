"""Idempotent SEC-only registration/default portfolio; no history replay."""

import argparse
import json
import sqlite3
from pathlib import Path

from doxagent.message_bus_v2.schema import DefaultProfileEntry, PollingConfig, UpdateActor
from doxagent.message_bus_v2.sec_edgar import SOURCE_ID, initial_source


def configure(service):
    repo = service.repository
    report = {"source_created": False, "profile_changed": False, "bindings_created": []}
    with repo.atomic_intake():
        source = repo.get_source(SOURCE_ID)
        if source is None:
            service.register_source(initial_source())
            report["source_created"] = True
        elif source.adapter_ref != initial_source().adapter_ref:
            raise ValueError("existing SEC source has incompatible adapter")
        profile = repo.get_default_profile("default")
        if profile and not any(e.source_id == SOURCE_ID for e in profile.entries):
            service.save_default_profile(
                profile.model_copy(
                    update={
                        "entries": [
                            *profile.entries,
                            DefaultProfileEntry(
                                source_id=SOURCE_ID,
                                polling=PollingConfig(target_interval_seconds=30),
                            ),
                        ],
                        "updated_by": UpdateActor.SYSTEM,
                        "updated_reason": "universal SEC EDGAR official filing source",
                    }
                )
            )
            report["profile_changed"] = True
        before = {b.binding_id for b in repo.list_bindings(source_id=SOURCE_ID)}
        service._ensure_sec_bindings()
        bindings = repo.list_bindings(source_id=SOURCE_ID)
        report["bindings_created"] = [b.binding_id for b in bindings if b.binding_id not in before]
        report["bindings"] = [
            {
                "id": b.binding_id,
                "enabled": b.enabled,
                "interval": b.polling.target_interval_seconds,
            }
            for b in bindings
        ]
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
            report = configure(MessageBusV2Service(repo))
        finally:
            repo.close()
    else:
        with sqlite3.connect(Path(args.bus).resolve().as_uri() + "?mode=ro", uri=True) as db:
            report = {
                "applied": False,
                "tickers": [
                    r[0]
                    for r in db.execute(
                        "SELECT ticker FROM ticker_monitoring_states ORDER BY ticker"
                    )
                ],
                "source_exists": bool(
                    db.execute(
                        "SELECT 1 FROM source_definitions WHERE source_id=?", (SOURCE_ID,)
                    ).fetchone()
                ),
            }
    Path(args.receipt).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
