"""Read-only production query diagnosis; never persists a token or business data."""
import copy
import json
import sqlite3
import time
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
from doxagent.api_v2.app import create_app, PREFIX
from doxagent.api_v2.auth import Principal
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.query_budget import deadline, frozen_proofs
from doxagent.v2_control.repository import ControlRepository
from doxagent.persistent_runtime_v2.journal import RuntimeJournal


class Auth:
    async def authenticate(self, token):
        return Principal("diagnosis", "DEVELOPER", time.time() + 3600)


queries = []
plans = []


class Cursor:
    def __init__(self, cursor, record):
        self.cursor, self.record = cursor, record

    def measured(self, callback):
        started = time.monotonic()
        try:
            return callback()
        finally:
            self.record["ms"] += (time.monotonic() - started) * 1000

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def fetchone(self):
        return self.measured(self.cursor.fetchone)

    def fetchall(self):
        return self.measured(self.cursor.fetchall)


class DB:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, parameters=()):
        record = {"sql": " ".join(sql.split()), "ms": 0}
        queries.append(record)
        if "UNION ALL" in sql:
            plans.append({"sql": record["sql"], "plan": [r[3] for r in self.db.execute("EXPLAIN QUERY PLAN " + sql, parameters).fetchall()]})
        started = time.monotonic()
        try:
            return Cursor(self.db.execute(sql, parameters), record)
        finally:
            record["ms"] += (time.monotonic() - started) * 1000


class TracedStore(ReadStore):
    @contextmanager
    def connect(self, *, write=False, timeout=2):
        if write:
            raise AssertionError("Production writes prohibited")
        with super().connect(timeout=timeout) as db:
            yield DB(db)


store = TracedStore("/data/read/v2.sqlite3")
with sqlite3.connect("file:/data/read/v2.sqlite3?mode=ro", uri=True) as db:
    row = db.execute("SELECT seq,payload FROM views WHERE json_extract(payload,'$.wire.page')='OVERVIEW' ORDER BY expires_at DESC LIMIT 1").fetchone()
    snapshot = json.loads(row[1])
    head = ReadStore.highwater(db)
    print(json.dumps({"view_seq": row[0], "head": head, "as_of": snapshot["as_of"], "tickers": snapshot["tickers"], "period": snapshot["wire"]["period"]}), flush=True)
    recent = db.execute("SELECT seq,json_extract(payload,'$.as_of'),json_extract(payload,'$.wire.period.selected') FROM views WHERE json_extract(payload,'$.wire.page')='OVERVIEW' ORDER BY expires_at DESC LIMIT 10").fetchall()
    print(json.dumps({"recent_overview_views": recent}), flush=True)
    old = db.execute("SELECT payload FROM views WHERE json_extract(payload,'$.wire.page')='OVERVIEW' AND json_extract(payload,'$.as_of')<='2026-10-02T08:52:00Z' ORDER BY expires_at DESC LIMIT 1").fetchone()
    old_snapshot = json.loads(old[0]) if old else None

control = ControlRepository(RuntimeJournal("/data/runtime/runtime.sqlite3", initialize=False))
app = create_app(store=store, control=control, auth=Auth())
active = copy.deepcopy(snapshot)


def view_get(owner, identity, ticker=None):
    frozen_proofs.set((active["seq"], active.get("capture_proofs")))
    return active


app.state.views.get = view_get


@app.middleware("http")
async def budget(request, call_next):
    deadline.set(time.monotonic() + 20)
    return await call_next(request)


with TestClient(app) as client:
    for mode in ("latest_saved_view",) * 5:
        if mode == "incident_saved_view" and old_snapshot is None:
            continue
        active = copy.deepcopy(old_snapshot if mode == "incident_saved_view" else snapshot)
        if mode == "head_at_start":
            active["seq"] = head
        for path in ("/overview/tickers", "/overview/metrics"):
            queries.clear()
            plans.clear()
            started = time.monotonic()
            response = client.get(PREFIX + path, params={"view_id": "diagnostic"}, headers={"Authorization": "Bearer diagnostic"})
            print(json.dumps({"mode": mode, "seq": active["seq"], "path": path, "status": response.status_code, "elapsed_ms": round((time.monotonic() - started) * 1000, 2), "query_count": len(queries), "slow_queries": sorted(queries, key=lambda q: q["ms"], reverse=True)[:8], "plans": copy.deepcopy(plans)}, ensure_ascii=False), flush=True)
