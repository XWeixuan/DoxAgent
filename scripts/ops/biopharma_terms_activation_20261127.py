"""Apply the five user-supplied terms through the existing owner; no new subscriptions."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from doxagent.message_bus_v2.monitoring_terms import (
    MonitoringTermsService,
    TickerMonitoringTerms,
    validate_terms,
    language_requirements,
)
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings

TICKERS = ("CGEM", "RGNX", "PRAX", "XENE", "SMMT")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    os.umask(0o077)
    repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
    owner = MonitoringTermsService(repo)
    bus = MessageBusV2Service(repo)
    inputs, before = {}, {}
    for ticker in TICKERS:
        raw = (args.directory / (ticker + "_monitoring_terms_en.json")).read_bytes()
        terms = TickerMonitoringTerms.model_validate_json(raw)
        assert terms.ticker == ticker
        current = owner.get(ticker)
        same = bool(current and current[1].model_dump() == terms.model_dump())
        if not same and terms.expected_revision != (current[0] if current else 0):
            raise ValueError("Concurrent terms revision: " + ticker)
        validate_terms(terms, owner.required_languages(ticker))
        for lang in language_requirements(
            repo.list_sources(), repo.list_bindings(ticker=ticker), massive=True
        ):
            if not terms.massive_l2.get(lang) or not terms.massive_l2[lang].groups:
                raise ValueError("Missing Massive rules: " + ticker + ":" + lang)
        state = repo.get_ticker_state(ticker)
        if not state or state.status.value != "running":
            raise ValueError("Ticker activation must already be running: " + ticker)
        before[ticker] = {
            "terms": current[1].model_dump(mode="json") if current else None,
            "revision": current[0] if current else 0,
            "state": state.model_dump(mode="json"),
            "bindings": [b.model_dump(mode="json") for b in repo.list_bindings(ticker=ticker)],
        }
        inputs[ticker] = (terms, hashlib.sha256(raw).hexdigest(), same)
    receipt = {"at": utc_now().isoformat(), "applied": args.apply, "tickers": {}}
    if args.apply:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        backup = args.receipt.with_name("before.private.json")
        if not backup.exists():
            backup.write_text(json.dumps(before, ensure_ascii=False))
    with repo.atomic_intake():
        for ticker, (terms, digest, same) in inputs.items():
            changes = []
            if args.apply and not same:
                owner.apply(terms, actor="user:" + ticker + "_monitoring_terms_en.json")
            for b in repo.list_bindings(ticker=ticker):
                source = repo.get_source(b.source_id)
                if not source.enabled or not source.default_polling_config.enabled:
                    changes.append("source:" + source.source_id)
                    if args.apply:
                        bus.update_source(
                            source.source_id,
                            {
                                "enabled": True,
                                "default_polling_config": {
                                    **source.default_polling_config.model_dump(),
                                    "enabled": True,
                                },
                            },
                            actor=UpdateActor.USER,
                            reason="Enable existing approved biopharma subscriptions",
                        )
                if not b.enabled or not b.polling.enabled:
                    changes.append("binding:" + b.binding_id)
                    if args.apply:
                        bus.update_binding(
                            b.binding_id,
                            {
                                "enabled": True,
                                "polling": {**b.polling.model_dump(), "enabled": True},
                            },
                            actor=UpdateActor.USER,
                            reason="Enable existing approved biopharma subscriptions",
                        )
            found = owner.get(ticker)
            if args.apply:
                assert found and found[1].model_dump() == terms.model_dump(), ticker
                assert (
                    repo.get_ticker_state(ticker).model_dump(mode="json") == before[ticker]["state"]
                )
            receipt["tickers"][ticker] = {
                "file_sha256": digest,
                "revision": found[0] if found else 0,
                "semantic_equal": bool(found and found[1].model_dump() == terms.model_dump()),
                "binding_count": len(before[ticker]["bindings"]),
                "switch_changes": changes,
                "l1_count": len(terms.l1_concepts),
                "l2_groups": {k: len(v.groups) for k, v in terms.l2.items()},
                "massive_groups": {k: len(v.groups) for k, v in terms.massive_l2.items()},
                "x_accounts": terms.x_accounts,
            }
    receipt["completed_at"] = utc_now().isoformat()
    if args.apply:
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False))
    repo.close()


if __name__ == "__main__":
    main()
