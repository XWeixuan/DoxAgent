"""Restore missing frontend dates from successful maintenance evidence only."""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.repository import ReadStore

root = Path("/data/runtime/o2-reference-serving-20261106")
journal = RuntimeJournal(DoxAgentSettings().persistent_runtime_v2_sqlite_path, initialize=False)
store = ReadStore("/data/read/active.json")
mapper = FormalProjectors(store, event_root="/data/events")
original_ingest = store.ingest


def retry_ingest(*args, **kwargs):
    for attempt in range(8):
        try:
            return original_ingest(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 7:
                raise
            time.sleep(min(attempt + 1, 5))


store.ingest = retry_ingest
# Rank small source coordinates, hydrate only the endpoints needed for a missing day.
with journal.transaction(write=False) as db:
    entries = [
        dict(r)
        for r in db.execute(
            "SELECT key,json_extract(payload,'$.ticker') AS ticker,"
            "json_extract(payload,'$.semantic_day') AS day,"
            "json_extract(payload,'$.prepared_at') AS prepared FROM runtime_values "
            "WHERE namespace='reference_deliveries' "
            "AND json_extract(payload,'$.status')='SUCCEEDED' "
            "ORDER BY ticker,day,prepared,key"
        )
    ]
groups = {}
for entry in entries:
    groups.setdefault((entry["ticker"], entry["day"]), []).append(entry)
(root / "successful-days-plan.json").write_text(
    json.dumps(
        [
            {"ticker": t, "day": d, "first": v[0]["key"], "latest": v[-1]["key"]}
            for (t, d), v in groups.items()
        ],
        indent=2,
    )
)
results = []
for (ticker, day), entries in groups.items():
    before = store.get("delta_day", ticker, day)
    if before and before["state"] in ("AVAILABLE", "NO_CHANGE"):
        continue
    try:
        endpoints = {entries[0]["key"]: entries[0], entries[-1]["key"]: entries[-1]}
        for key in endpoints:
            value = journal.get("reference_deliveries", key)
            assert value["status"] == "SUCCEEDED" and value.get("control_epoch") is not None
            packet = {
                "source": "runtime",
                "entity_id": json.dumps(["reference_deliveries", key]),
                "table_name": "runtime_values",
                "operation": "BACKFILL",
                "seq": 0,
                "recorded_at": value["settled_at"],
                "row": {
                    "namespace": "reference_deliveries",
                    "key": key,
                    "payload": json.dumps(value),
                },
            }
            records, metrics = mapper(packet)
            store.ingest(
                "o2-reference-serving-reconcile-20261106", key, records, contributions=metrics
            )
        after = store.get("delta_day", ticker, day)
        assert after and after["state"] in ("AVAILABLE", "NO_CHANGE"), after
        result = {"ticker": ticker, "day": day, "state": after["state"]}
    except Exception as exc:
        result = {"ticker": ticker, "day": day, "error": str(exc)}
    results.append(result)
    print(json.dumps(result), flush=True)
(root / "reconcile-receipt.json").write_text(
    json.dumps(
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "successful_source_days": len(groups),
            "repaired_days": len(results),
            "failed": sum("error" in r for r in results),
            "results": results,
        },
        indent=2,
    )
)
assert not any("error" in r for r in results)
