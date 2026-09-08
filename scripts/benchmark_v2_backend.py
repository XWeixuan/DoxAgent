"""Reproducible offline local-read benchmark; no production or external calls."""

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from doxagent.api_v2.streaming import MessageStreams
from doxagent.api_v2.views import Views
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.repository import ReadStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.v2_backend.test_streaming import message  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"machine": platform.platform(), "python": platform.python_version(),
              "scope": "OFFLINE_LOCAL_READS", "measurements": []}
    with tempfile.TemporaryDirectory(prefix="v2-benchmark-") as directory:
        store = ReadStore(Path(directory) / "read.db")
        store.migrate()
        body = store.put_content("MU", "中文 research body " * 200000, "text/plain")
        views = Views(store, PageCalendar())
        streams = MessageStreams(store, views)
        for size, begin in ((10000, 0), (100000, 10000)):
            for offset in range(begin, size, 500):
                rows = []
                for i in range(offset, offset + 500):
                    row = message(f"m-{i:09}")
                    row["data"]["body"] = body
                    row["search"] = "keyword" if i % 10 == 0 else "ordinary"
                    rows.append(row)
                    rows.append({"kind": "case", "ticker": "MU", "id": row["id"],
                                 "sort": row["id"], "day": "2026-09-08", "data": {"status": "COMPLETED"}})
                seq = store.ingest("fixture", str(offset), rows, contributions=[
                    {"metric": "messages", "ticker": "MU", "entity": r["id"], "day": r["day"], "value": 1}
                    for r in rows if r["kind"] == "message"])
            view = store.save_token("benchmark", "benchmark", {"seq": seq, "wire": {
                "ticker": "MU", "page": "MESSAGE_BUS", "period": {"current": {
                    "membership": "LISTED_TRADING_DAYS", "trading_days": ["2026-09-08"]}}}}, view=True)
            def forbidden(*args, **kwargs):
                raise AssertionError("summary path read a full content object")
            store.content = forbidden
            for name, query in (
                ("first_page", lambda: store.page("message", "MU", seq, limit=20)),
                ("filtered_page", lambda: store.page("message", "MU", seq, limit=20, q="keyword")),
                ("all_metric", lambda: str(Metrics(store).value("messages", ["MU"], seq, days=None))),
                ("stream_baseline", lambda: streams.baseline("benchmark", "MU", {"view_id": view, "limit": "20"})),
            ):
                durations = []
                for _ in range(7):
                    start = time.perf_counter()
                    result = query()
                    durations.append((time.perf_counter() - start) * 1000)
                length = len(json.dumps(result, ensure_ascii=False).encode())
                assert length < 256 * 1024
                report["measurements"].append({"rows_per_kind": size, "query": name,
                    "median_ms": statistics.median(durations), "max_ms": max(durations),
                    "response_bytes": length, "business_remote_calls": 0, "full_body_reads": 0})
            with store.connect() as db:
                report.setdefault("query_plans", {})[str(size)] = [list(r) for r in db.execute(
                    "EXPLAIN QUERY PLAN SELECT payload FROM objects WHERE kind='message' AND ticker='MU' "
                    "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) ORDER BY sort_key DESC,id DESC LIMIT 20", (seq, seq))]
            print(f"benchmarked {size} messages and {size} Cases", flush=True)
    report["at"] = datetime.now(UTC).isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
