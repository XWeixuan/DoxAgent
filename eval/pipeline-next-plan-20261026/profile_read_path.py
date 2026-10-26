"""Bounded read/prepare replay of completed captures; never commit or enqueue."""
import json
import sqlite3
import time
from contextvars import ContextVar
from pathlib import Path

from doxagent.message_bus_v2.polling import PollJournal
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent

settings = DoxAgentSettings()
repo = MessageBusV2Repository.__new__(MessageBusV2Repository)
repo.path = Path(settings.message_bus_v2_sqlite_path)
repo.content = NativeContent(repo.path)
repo._atomic_connection = ContextVar("probe_atomic", default=None)
repo._scope_connection = ContextVar("probe_scope", default=None)
journal = PollJournal.__new__(PollJournal)
journal.repo = repo
with sqlite3.connect(repo.path.as_uri()+"?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    selected = []
    for source in ("nbis_official_actor_rss", "barrons_ticker_news", "google_news_search_rss"):
        row = db.execute("SELECT capture_id,source_id FROM poll_captures WHERE source_id=? "
                         "AND status='COMPLETED' AND message_count>0 ORDER BY observed_at DESC LIMIT 1",
                         (source,)).fetchone()
        if row:
            selected.append(dict(row))
    duplicates = [dict(r) for r in db.execute(
        "SELECT p.source_id,count(*) captures,count(DISTINCT k.input_hash) hashes,sum(p.message_count) inputs "
        "FROM poll_captures p JOIN poll_capture_inputs k ON k.capture_id=p.capture_id "
        "WHERE p.observed_at>='2026-10-25T20:55:00+00:00' AND p.observed_at<'2026-10-25T21:04:45+00:00' "
        "AND k.input_hash!='' GROUP BY p.source_id")]
out = {"duplicates": duplicates, "replays": []}
for capture in selected:
    for offset in (0, 16, 32):
        wall, cpu = time.monotonic(), time.thread_time()
        row = journal.load(capture["capture_id"])
        load_seconds = time.monotonic()-wall
        row["intake_position"] = min(offset, row["message_count"]-1)
        prepared = journal.prepare_piece(row)
        out["replays"].append({"source": capture["source_id"], "messages": row["message_count"],
            "offset": row["intake_position"], "expanded_json_bytes": len(row["payload_json"]),
            "load_seconds": load_seconds, "wall_seconds": time.monotonic()-wall,
            "cpu_seconds": time.thread_time()-cpu, "advanced": prepared[4].acquisition_metadata["intake_advanced"],
            "candidates": len(prepared[4].messages)})
print(json.dumps(out))
