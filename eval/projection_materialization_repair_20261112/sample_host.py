"""Fifteen-minute, metadata-only live sampling; never hold a cross-sample snapshot."""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

paths = {"research": "/var/lib/docker/volumes/doxagent-v2_v2-data/_data/research/research.sqlite3", "initialization": "/var/lib/docker/volumes/doxagent-v2_v2-data/_data/initialization/control.sqlite3", "bus": "/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/bus.sqlite3", "runtime": "/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3"}
read_path = Path("/var/lib/docker/volumes/doxagent-v2_v2-data/_data/read/serving-v4-20261018/v2.sqlite3")
def connect(path):
    db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True, timeout=.2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db

for index in range(61):
    start = time.monotonic()
    out = {"at": datetime.now(timezone.utc).isoformat(), "index": index, "sources": {}}
    try:
        with connect(read_path) as db:
            cp = dict(db.execute("SELECT source,position FROM checkpoints"))
            out["highwater"] = int(db.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])
            out["health"] = [dict(r) for r in db.execute("SELECT * FROM source_health")]
            out["gaps"] = [dict(r) for r in db.execute("SELECT source,reason,count(*) n FROM gaps GROUP BY source,reason")]
            h = out["highwater"]
            out["recent_commits"] = [dict(r) for r in db.execute("SELECT c.seq,c.source,c.source_event,c.at,r.projected_at FROM commits c LEFT JOIN commit_retention r USING(seq) WHERE c.seq>? ORDER BY c.seq", (h-60,))]
        for source, path in paths.items():
            with connect(path) as db:
                position = cp.get(source,0)
                head = db.execute("SELECT head FROM v2_source_state WHERE source=?",(source,)).fetchone()[0]
                row = db.execute("SELECT seq,table_name,recorded_at FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT 1", (position,)).fetchone()
                out["sources"][source] = {"checkpoint": position, "head": head, "coordinate_distance": head-position, "next_hot": dict(row) if row else None}
        out["read_files"] = {str(read_path)+s: Path(str(read_path)+s).stat().st_size for s in ("", "-wal", "-shm") if Path(str(read_path)+s).exists()}
    except Exception as exc:
        out["error"] = type(exc).__name__ + ":" + str(exc)
    out["probe_ms"] = (time.monotonic()-start)*1000
    print(json.dumps(out), flush=True)
    if index < 60:
        time.sleep(max(0,30-(time.monotonic()-start)))
