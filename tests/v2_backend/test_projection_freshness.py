"""Throughput regressions must preserve ordering, incoming state and unknowns."""
import json
import sqlite3
import sys
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from doxagent.v2_read import cli, health
from doxagent.v2_read.lifecycle import direction_evidence, direction_summary
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.source_lifecycle import sync_business_facts
from doxagent.v2_read.repository import ReadStore, encode
from tests.v2_backend.test_source_lifecycle import fixture_source


def counted_connections(store, monkeypatch):
    calls = []
    original = store.connect

    @contextmanager
    def connect(**kwargs):
        calls.append(1)
        with original(**kwargs) as db:
            yield db

    monkeypatch.setattr(store, "connect", connect)
    return calls


def test_health_one_snapshot_and_incoming_poll_and_binding_deletions(tmp_path, monkeypatch):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    def row(kind, identity, data, ticker="MU"):
        return dict(kind="native:" + kind, ticker=ticker, id=identity, data=data)
    store.ingest("seed", "1", [
        row("v2_ticker_control", "MU", dict(analysis_allowed=True, activation_id="a", epoch=1)),
        *(row("v2_control_ack", "1:" + role, {}) for role in ("initializer", "bus", "runtime")),
        row("ticker_monitoring_states", "MU", dict(status="running")),
        row("ticker_source_bindings", "MU:s", dict(enabled=True, source_id="s")),
        row("source_definitions", "s", dict(enabled=True), ticker=""),
        row("poll_states", "MU:s", dict(status="failed")),
    ])
    # Non-empty acks, as in real capture receipts.
    store.ingest("seed", "2", [row("v2_control_ack", "1:" + role, dict(epoch=1))
                               for role in ("initializer", "bus", "runtime")])
    monkeypatch.setattr("doxagent.v2_read.projectors.state_wire", lambda state: dict(
        ticker="MU", run_state="RUNNING", health="UNKNOWN", health_reasons=[],
        initialization_incomplete=False, removed=False))
    calls = counted_connections(store, monkeypatch)
    monkeypatch.setattr(store, "get", lambda *a, **kw: pytest.fail("per-object health read"))
    for incoming, expected in [
        ([row("poll_states", "MU:s", dict(status="failed"))], "DEGRADED"),
        ([row("poll_states", "MU:s", dict(status="succeeded"))], "NORMAL"),
        ([row("ticker_source_bindings", "MU:s", None)], "NORMAL"),
        ([row("source_definitions", "s", dict(enabled=False), ticker="")], "NORMAL"),
    ]:
        calls.clear()
        health.project(store, "MU", incoming)
        assert len(calls) == 1
        assert next(r["data"]["health"] for r in incoming if r["kind"] == "ticker") == expected


def test_authorization_head_batch_does_not_connect_per_policy(tmp_path, monkeypatch):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("seed", "catalog", [dict(kind="policy_catalog", ticker="MU", id=f"P{i}:rev",
        data=dict(policy_id=f"P{i}", directions=["LONG", "SHORT"], lifecycle="ACTIVE"))
        for i in range(500)])
    store.ingest("seed", "usage", [dict(kind="policy_direction_usage", ticker="MU",
        id=encode(["MU", "P1", "LONG"]), data=dict(consumed=True))])
    calls = counted_connections(store, monkeypatch)
    records, _ = DomainProjectors(store)(dict(table_name="runtime_values", operation="UPDATE",
        seq=1, entity_id="head", recorded_at="2026-10-19T00:00:00Z",
        row=dict(namespace="policy_authorization_head", key="MU", payload=json.dumps({}))))
    assert len(calls) < 8  # Independent of the number of Policy revisions.
    summary = next(r["data"] for r in records if r["kind"] == "policy_catalog" and r["id"] == "P1:rev")
    assert summary["consumed_directions"] == ["LONG"]
    assert summary["executable_directions"] == ["SHORT"]


def test_direction_snapshot_keeps_migration_block_and_pending_usage(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("seed", "migration", [dict(kind="native:runtime_values", ticker="MU",
        id="policy_consumption_migration:old", data=dict(policy_id="P1", status="UNRESOLVED"))])
    result = direction_summary(store, "MU", dict(policy_id="P1", directions=["LONG"], lifecycle="ACTIVE"),
        evidence=direction_evidence(store, "MU"), admitted=True,
        pending_state=dict(direction="LONG", consumed=True))
    assert result["consumed"]["value"] is True
    assert result["effective"]["value"] is False
    assert result["executable_directions"] == []


def test_batch_reader_reuses_schema_without_pinning_source_snapshot(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    previous = source.head()
    with source.connection(write=True) as db:
        db.execute("INSERT INTO poll_states VALUES('a','s','MU','succeeded','1','{}')")
    first = source.coordinates(previous, 100)[0]
    connections = []
    original = source.connection
    def connection(**kwargs):
        db = original(**kwargs); connections.append(db); return db
    monkeypatch.setattr(source, "connection", connection)
    with source.event_reader() as load:
        assert load(first)["row"]["binding_id"] == "a"
        assert not connections[0].in_transaction
        with original(write=True) as db:
            db.execute("INSERT INTO poll_states VALUES('b','s','MU','succeeded','1','{}')")
        second = source.coordinates(first, 100)[0]
        assert load(second)["row"]["binding_id"] == "b"
    assert len(connections) == 2  # One reader plus the explicit coordinates query.
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


def test_projector_does_not_sleep_between_productive_ticks(tmp_path, monkeypatch):
    counts = iter([10, 0])
    sleeps = []
    repairs = []
    def tick(self, **kwargs):
        try:
            return next(counts)
        except StopIteration:
            raise SystemExit(0)
    monkeypatch.setattr(cli.ProjectionWorker, "tick", tick)
    monkeypatch.setattr(cli.ProjectionWorker, "repair", lambda self, **kwargs: repairs.append(kwargs))
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    monkeypatch.setattr(sys, "argv", ["projector", "project", "--read-db", str(tmp_path / "read.db")])
    with pytest.raises(SystemExit):
        cli.main()
    assert sleeps == [1]
    assert repairs == [{"limit": 100}]


def test_projection_indexes_are_explicit_idempotent_and_cover_hot_predicates(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_projection_indexes()
    store.migrate_projection_indexes()
    queries = [
        ("current_kind_revision", "SELECT * FROM object_current WHERE kind='policy_catalog' AND valid_from>10"),
        ("contribution_metric_revision", "SELECT * FROM contributions_current WHERE metric='new_fact_candidates' AND valid_from>10"),
        ("current_graph_case_members", "SELECT parent,day FROM object_current WHERE kind='graph_member' AND ticker='MU' AND json_extract(payload,'$.case_id')='c'"),
        ("legacy_policy_coverage", "SELECT * FROM object_current WHERE kind='policy_catalog' AND json_type(payload,'$.directions') IS NULL AND json_extract(payload,'$.consumed.value')=0"),
    ]
    with store.connect() as db:
        for index, query in queries:
            assert index in " ".join(str(tuple(r)) for r in db.execute("EXPLAIN QUERY PLAN " + query))


def test_bus_only_progress_does_not_take_runtime_writer_for_business_sync(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    source.source = "runtime"
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    source.claim_serving_facts(store.path)
    sync_business_facts(source, store)
    original = source.connection
    def connection(*, write=False):
        assert not write, "unrelated Bus commit must not acquire Runtime writer"
        return original()
    monkeypatch.setattr(source, "connection", connection)
    store.ingest("bus", "poll", [dict(kind="native:poll_states", ticker="MU", id="b", data=dict(status="succeeded"))])
    sync_business_facts(source, store)


def test_acknowledgement_noop_is_coalesced_but_progress_and_gaps_are_not(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    calls=[]
    original=source.acknowledge_exceptions
    def ack(*args):
        calls.append(args);return original(*args)
    monkeypatch.setattr(source, "acknowledge_exceptions", ack)
    worker=ProjectionWorker(store,[source],lambda event:([],[]))
    worker.tick(limit=500)
    first=len(calls)
    worker.tick(limit=500)
    assert len(calls)==first
    with source.connection(write=True) as db:
        db.execute("INSERT INTO poll_states VALUES('fresh','s','MU','succeeded','1','{}')")
    worker.tick(limit=500)
    assert len(calls)==first+1
    with store.connect(write=True) as db:
        db.execute("INSERT INTO gaps(source,event,reason,next_attempt_at) VALUES('bus','1','TEST','9999')")
    worker.tick(limit=500)
    assert len(calls)==first+2


@pytest.mark.parametrize("budget,expected", [(1,2),(5,9)])
def test_event_work_budget_is_bounded_and_preserves_order(monkeypatch,budget,expected):
    times=iter(i*0.6 for i in range(100))
    monkeypatch.setattr("doxagent.v2_read.projector.time.monotonic",lambda:next(times))
    committed=[]
    store=SimpleNamespace(ingest=lambda source,event,*args,**kwargs:committed.append(int(event)))
    worker=ProjectionWorker(store,[],lambda event:([],[]))
    count=worker._events(SimpleNamespace(source="bus"),list(range(1,30)),
        lambda n:dict(seq=n,recorded_at="2026-10-19T00:00:00Z"),budget=budget)
    assert count==expected and committed==list(range(1,expected+1))


def test_projection_session_reuses_idle_connections_without_delaying_commits(tmp_path, monkeypatch):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    opened=[];original=sqlite3.connect
    def connect(*args,**kwargs):
        db=original(*args,**kwargs);opened.append(db);return db
    monkeypatch.setattr(sqlite3,'connect',connect)
    with store.projection_session():
        for i in range(20):
            store.ingest('bus',str(i),[dict(kind='native:poll_states',ticker='MU',id='b',data=dict(count=i))])
            assert store.get('native:poll_states','MU','b')['count']==i
            # A different process/connection can already see each event commit.
            with original(store.path) as external:
                assert external.execute('select max(seq) from commits').fetchone()[0]==i+1
        assert len(opened)==2
        with store.connect() as outer:
            assert store.get('native:poll_states','MU','b')['count']==19
            assert outer.in_transaction
        assert len(opened)==3  # Nested readers never reuse a checked-out snapshot.
        with pytest.raises(ValueError):
            with store.connect(write=True) as db:
                db.execute("DELETE FROM object_current")
                raise ValueError('abort this event only')
        assert store.get('native:poll_states','MU','b')['count']==19
        assert all(not db.in_transaction for pool in store._projection_connections.values() for db in pool)
    assert store._projection_connections is None
    for db in opened:
        with pytest.raises(sqlite3.ProgrammingError):db.execute('select 1')
