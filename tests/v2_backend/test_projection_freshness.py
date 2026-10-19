"""Throughput regressions must preserve ordering, incoming state and unknowns."""
import json
import sqlite3
import sys
from contextlib import contextmanager

import pytest

from doxagent.v2_read import cli, health
from doxagent.v2_read.lifecycle import direction_evidence, direction_summary
from doxagent.v2_read.projectors import DomainProjectors
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
    def tick(self, **kwargs):
        try:
            return next(counts)
        except StopIteration:
            raise SystemExit(0)
    monkeypatch.setattr(cli.ProjectionWorker, "tick", tick)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    monkeypatch.setattr(sys, "argv", ["projector", "project", "--read-db", str(tmp_path / "read.db")])
    with pytest.raises(SystemExit):
        cli.main()
    assert sleeps == [1]
