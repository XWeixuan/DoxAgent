"""Read a fixed ledger window by seq seeks; avoid a global timestamp scan."""
import json
import sqlite3
import time

from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
db = sqlite3.connect("file:" + settings.message_bus_v2_sqlite_path + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row
deadline = time.monotonic() + 15
db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
first = db.execute("SELECT seq,recorded_at FROM v2_source_outbox ORDER BY seq LIMIT 1").fetchone()
last = db.execute("SELECT seq,recorded_at FROM v2_source_outbox ORDER BY seq DESC LIMIT 1").fetchone()


def boundary(stamp):
    lo, hi = first["seq"], last["seq"] + 1
    while lo < hi:
        mid = (lo+hi)//2
        row = db.execute("SELECT seq,recorded_at FROM v2_source_outbox WHERE seq>=? ORDER BY seq LIMIT 1", (mid,)).fetchone()
        if row is None or row["recorded_at"] >= stamp:
            hi = mid
        else:
            lo = row["seq"]+1
    return lo


begin, end = boundary("2026-10-25T20:55:00.000Z"), boundary("2026-10-25T21:04:45.000Z")
out = {"seq_start": begin, "seq_end": end, "retained_first": dict(first), "retained_last": dict(last)}
out["tables"] = [dict(r) for r in db.execute(
    "SELECT table_name,count(*) n FROM v2_source_outbox WHERE seq>=? AND seq<? GROUP BY table_name",
    (begin, end))]
print(json.dumps(out))
