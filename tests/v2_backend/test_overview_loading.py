from contextlib import contextmanager
from datetime import UTC, datetime
import json

import pytest

from doxagent.api_v2.views import Views
from doxagent.api_v2.graph import Graphs
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.content_files import ContentFiles
from doxagent.v2_read.repository import ReadStore


def test_latest_message_batch_is_bounded_by_ticker_and_preserves_snapshot(tmp_path, monkeypatch):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_projection_indexes()
    def message(ticker, identity, sort, at):
        return dict(kind="message", ticker=ticker, id=identity, sort=sort,
                    data=dict(stream_published_at=at, body="large body " * 2000))
    seq = store.ingest("fixture", "seed", [
        message("MU", "z", "same", "frozen"), message("MU", "a", "same", "tie-loser"),
        *[message("BE", str(i), str(i), "unrelated") for i in range(2000)],
    ])
    later = store.ingest("fixture", "update", [
        message("MU", "z", "updated", "revised"), message("MU", "new", "zz-new", "new"),
    ])
    original = store.connect
    connections, progress = [], []
    @contextmanager
    def bounded(*args, **kwargs):
        with original(*args, **kwargs) as db:
            connections.append(1)
            # A global message scan/sort exceeds this budget even on a tiny fixture.
            def check():
                progress.append(1)
                return int(len(progress) > 100)
            db.set_progress_handler(check, 100)
            yield db
    monkeypatch.setattr(store, "connect", bounded)
    monkeypatch.setattr(ContentFiles, "read", lambda *args: pytest.fail("timestamp read opened a body"))
    assert store.latest_messages_at(["MU", "NONE"], seq) == {"MU": "frozen", "NONE": None}
    assert len(connections) == 1
    assert store.latest_messages_at(["MU"], later) == {"MU": "new"}
    assert store.latest_messages_at([], later) == {}


@pytest.mark.parametrize("closed,pending,expected", [
    (False, True, "PRE_MARKET"), (True, True, "CLOSED_MAINTENANCE"),
    (True, False, "CLOSED_SLEEP"),
])
def test_context_only_scans_maintenance_when_it_can_change_the_clock(tmp_path, closed, pending, expected):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    now = datetime(2026, 10, 18 if closed else 20, 9, tzinfo=UTC)
    store.ingest("fixture", "seed", [
        dict(kind="ticker", ticker="MU", id="MU", data=dict(removed=False)),
        dict(kind="native:runtime_tasks", ticker="MU", id="task", data=dict(id="task", status="PENDING")),
    ])
    # Legacy rows contain the due/control fields used by the existing clock rule.
    with store.connect(write=True) as db:
        db.execute("UPDATE object_current SET payload=? WHERE kind='native:runtime_tasks'", (
            json.dumps(dict(kind="MAINTENANCE", status="PENDING" if pending else "COMPLETED",
                            due_at="2026-10-17T00:00:00Z", inputs=dict(control_epoch=1))),))
        db.execute("INSERT INTO source_health(source,head,checkpoint,checked_at,error) VALUES(?,?,?,?,?)", (
            "runtime", 1, 1, now.isoformat(), None))
    statements = []
    original = store.connect
    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as db:
            db.set_trace_callback(statements.append)
            yield db
    store.connect = traced
    result = Views(store, PageCalendar()).create("owner", "OVERVIEW", None, "TRADING_DAYS_7", now=now)
    assert result["clock"]["session"]["value"] == expected
    maintenance = [s for s in statements if "native:runtime_tasks" in s]
    assert bool(maintenance) == closed


@pytest.mark.parametrize("frozen", [False, True])
def test_shared_runtime_reads_keep_ordered_ticker_seeks(tmp_path, monkeypatch, frozen):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_projection_indexes()
    day = "2026-10-19"
    def case(ticker, identity, sort):
        return dict(kind="case", ticker=ticker, id=identity, sort=sort, day=day,
                    data=dict(case_id=identity, semantic_day=day))
    def observation(ticker, identity, at):
        return dict(kind="graph_observation", ticker=ticker, id=identity, parent="W2",
                    sort=at, day=day, data=dict(latest_at=at))
    old_at, new_at = "2026-10-19T10:00:00Z", "2026-10-19T11:00:00Z"
    seq = store.ingest("fixture", "seed", [
        case("MU", "z", "same"), case("MU", "a", "same"),
        observation("MU", "w2", old_at),
        *[case("BE", str(i), str(i)) for i in range(2000)],
        *[observation("BE", str(i), old_at) for i in range(2000)],
    ])
    later = store.ingest("fixture", "update", [
        case("MU", "z", "updated"), observation("MU", "w2", new_at),
    ])
    original, progress = store.connect, []
    @contextmanager
    def bounded(*args, **kwargs):
        with original(*args, **kwargs) as db:
            def check():
                progress.append(1)
                return int(len(progress) > 100)
            db.set_progress_handler(check, 100)
            yield db
    monkeypatch.setattr(store, "connect", bounded)
    selected = seq if frozen else later
    rows = store.page("case", "MU", selected, days=[day], limit=1)
    assert [(r["id"], r["sort"]) for r in rows] == [("z", "same" if frozen else "updated")]
    assert [r["id"] for r in store.page("case", "MU", selected, days=[day],
                                      after=(rows[0]["sort"], rows[0]["id"]), limit=1)] == ["a"]
    nodes, _ = Graphs(store, None).counts("MU", selected, [day])
    w2 = next(node for node in nodes if node["node_id"] == "W2")
    assert w2["latest_processed_at"]["value"] == (old_at if frozen else new_at)
