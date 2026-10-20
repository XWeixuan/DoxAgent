"""Default read-only preview; --apply uses the Bus owner service under one CAS transaction."""

import argparse
import json
import sqlite3
from pathlib import Path

from doxagent.message_bus_v2.corporate_ir import SOURCE_ID
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints, initial_source
from doxagent.message_bus_v2.schema import UpdateActor, binding_id_for

TICKERS = ("INTC", "MU", "BE", "COHR", "NBIS", "CEG", "RKLB", "SNOW", "ONDS")


def preview(source, bindings, ticker_states):
    catalog = (source or initial_source()).default_parameters["endpoints"]
    if not all(t in ticker_states for t in TICKERS):
        raise ValueError("all nine tickers must already be registered")
    indexed = {b.binding_id: b for b in bindings}
    changes = []
    overlaps = []
    expected = {s.endpoint_id for s in initial_endpoints()}
    if not expected.issubset({s["endpoint_id"] for s in catalog}):
        raise ValueError("existing IR catalog is missing required endpoints; review its revision")
    known_urls = {s.entry_url.rstrip("/") for s in initial_endpoints()}
    known_urls.add("https://investors.rocketlabcorp.com/news")
    # Verified against all 20 article IDs/titles/dates and a real detail redirect.
    known_urls.add("https://ir.ondas.com/press-releases/rss")
    for ticker in TICKERS:
        current = indexed.get(binding_id_for(ticker, SOURCE_ID))
        ids = [s["endpoint_id"] for s in catalog if s["owner_ticker"] == ticker and s["enabled"]]
        if current:
            ids = sorted(set(ids) | set(current.source_parameters.get("endpoint_ids", [])))
        changes.append(
            {
                "ticker": ticker,
                "binding_id": binding_id_for(ticker, SOURCE_ID),
                "expected_version": current.version if current else None,
                "endpoint_ids": ids,
                "action": "create"
                if not current
                else "unchanged"
                if current.enabled
                and current.polling.enabled
                and current.polling.target_interval_seconds == 30
                and not current.polling.active_windows
                and current.source_parameters.get("endpoint_ids") == ids
                else "update",
            }
        )
    for binding in bindings:
        if binding.ticker not in TICKERS or binding.source_id != "newswire_rss":
            continue
        removed = [
            u
            for u in binding.source_parameters.get("rss_urls", [])
            if str(u).rstrip("/") in known_urls
        ]
        if removed:
            overlaps.append(
                {
                    "binding_id": binding.binding_id,
                    "expected_version": binding.version,
                    "remove_urls": removed,
                }
            )
    return {
        "applied": False,
        "source_id": SOURCE_ID,
        "source_version": source.version if source else None,
        "bindings": changes,
        "overlaps": overlaps,
    }


def configure(service):
    repo = service.repository
    with repo.atomic_intake():
        report = preview(
            repo.get_source(SOURCE_ID),
            repo.list_bindings(),
            {s.ticker for s in repo.list_ticker_states()},
        )
        if not repo.get_source(SOURCE_ID):
            service.register_source(initial_source())
        elif not repo.get_source(SOURCE_ID).enabled:
            service.update_source(
                SOURCE_ID,
                {"enabled": True},
                actor=UpdateActor.USER,
                reason="Enable requested corporate IR",
            )
        for item in report["bindings"]:
            if item["action"] == "unchanged":
                continue
            parameters = {"endpoint_ids": item["endpoint_ids"]}
            current = repo.get_binding(item["binding_id"])
            if current:
                service.update_binding(
                    current.binding_id,
                    {
                        "enabled": True,
                        "source_parameters": parameters,
                        "polling": {
                            **current.polling.model_dump(mode="json"),
                            "enabled": True,
                            "target_interval_seconds": 30,
                            "active_windows": [],
                        },
                    },
                    actor=UpdateActor.USER,
                    reason="Requested corporate IR migration",
                )
            else:
                service.configure_binding(
                    ticker=item["ticker"],
                    source_id=SOURCE_ID,
                    source_parameters=parameters,
                    actor=UpdateActor.USER,
                    reason="Requested corporate IR",
                )
        for overlap in report["overlaps"]:
            binding = repo.get_binding(overlap["binding_id"])
            parameters = dict(binding.source_parameters)
            parameters["rss_urls"] = [
                u for u in parameters["rss_urls"] if u not in overlap["remove_urls"]
            ]
            service.update_binding(
                binding.binding_id,
                {
                    "source_parameters": parameters,
                    "enabled": binding.enabled and bool(parameters["rss_urls"]),
                },
                actor=UpdateActor.USER,
                reason="Move only overlapping feeds to corporate IR",
            )
        report["applied"] = True
        report["applied_source_version"] = repo.get_source(SOURCE_ID).version
        report["applied_binding_versions"] = {
            row["binding_id"]: repo.get_binding(row["binding_id"]).version
            for row in report["bindings"]
        }
        report["configured_endpoint_count"] = sum(
            len(s["endpoint_ids"]) for s in report["bindings"]
        )
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from doxagent.settings import DoxAgentSettings

    path = Path(DoxAgentSettings().message_bus_v2_sqlite_path).resolve()
    if args.apply:
        from doxagent.message_bus_v2.repository import MessageBusV2Repository
        from doxagent.message_bus_v2.service import MessageBusV2Service

        repo = MessageBusV2Repository(path)
        result = configure(MessageBusV2Service(repo, enrichment_queue_enabled=True))
    else:
        from doxagent.message_bus_v2.schema import SourceDefinition, TickerSourceBinding

        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT data_json FROM source_definitions WHERE source_id=?", (SOURCE_ID,)
            ).fetchone()
            source = SourceDefinition.model_validate_json(row[0]) if row else None
            bindings = [
                TickerSourceBinding.model_validate_json(r[0])
                for r in db.execute(
                    "SELECT data_json FROM ticker_source_bindings WHERE tombstoned_at IS NULL"
                )
            ]
            tickers = {r[0] for r in db.execute("SELECT ticker FROM ticker_monitoring_states")}
        result = preview(source, bindings, tickers)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
