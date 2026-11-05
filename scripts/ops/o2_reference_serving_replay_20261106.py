"""Replay accepted O2 reference evidence into the active production read generation."""

import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.repository import ReadStore

ROOT = Path("/data/runtime/o2-reference-serving-20261106")
ROOT.mkdir(parents=True, exist_ok=True)
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
plan = json.loads(Path("/data/runtime/o2-recovery-20261101/plan.json").read_text())
results = []
retry_failed = "--retry-failed" in sys.argv
failed_days = (
    {
        (r["ticker"], r["day"])
        for r in json.loads((ROOT / "receipt.json").read_text())["results"]
        if "error" in r
    }
    if retry_failed
    else set()
)


def emit(event, **fields):
    row = {"at": datetime.now(UTC).isoformat(), "event": event, **fields}
    with (ROOT / "replay.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


for ticker, entry in plan["tickers"].items():
    for day in entry["days"]:
        if retry_failed and (ticker, day) not in failed_days:
            continue
        identity = f"manual:o2-backfill:{ticker}:{day}:20261101"
        try:
            task = journal.get_task(identity)
            assert task and task["status"] == "SUCCEEDED", identity
            run = task["receipt"]["frame"]["run_id"]
            value = journal.get("reference_deliveries", run)
            assert value and value["status"] == "SUCCEEDED", run
            assert value["ticker"] == ticker and value["semantic_day"] == day
            before = store.get("delta_day", ticker, day)
            packet = {
                "source": "runtime",
                "table_name": "runtime_values",
                "entity_id": json.dumps(["reference_deliveries", run]),
                "operation": "BACKFILL",
                "recorded_at": value["settled_at"],
                "seq": 0,
                "row": {
                    "namespace": "reference_deliveries",
                    "key": run,
                    "payload": json.dumps(value),
                },
            }
            records, contributions = mapper(packet)
            seq = store.ingest(
                "o2-reference-serving-replay-20261106",
                run,
                records,
                at=datetime.now(UTC).isoformat(),
                contributions=contributions,
            )
            after = store.get("delta_day", ticker, day)
            assert after and after["state"] in ("AVAILABLE", "NO_CHANGE"), after
            assert store.get("reference_delta", ticker, after["delta_id"])
            result = {
                "ticker": ticker,
                "day": day,
                "maintenance_task": identity,
                "run_id": run,
                "before_state": before and before["state"],
                "after_state": after["state"],
                "read_seq": seq,
                "display_delta_id": after["delta_id"],
            }
            results.append(result)
            emit("replayed", **result)
        except Exception as exc:
            emit(
                "failed",
                ticker=ticker,
                day=day,
                error_type=type(exc).__name__,
                message=str(exc)[:1000],
            )
            results.append({"ticker": ticker, "day": day, "error": str(exc)})

receipt = {
    "finished_at": datetime.now(UTC).isoformat(),
    "read_path": str(store.path),
    "total": len(results),
    "failed": sum("error" in r for r in results),
    "results": results,
}
(ROOT / ("retry-receipt.json" if retry_failed else "receipt.json")).write_text(
    json.dumps(receipt, indent=2)
)
emit("replay_finished", total=receipt["total"], failed=receipt["failed"])
raise SystemExit(bool(receipt["failed"]))
