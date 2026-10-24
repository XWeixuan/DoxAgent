from datetime import UTC, datetime, timedelta

import pytest

from doxagent.v2_read.maintenance import atomic_json, collect
from doxagent.v2_read.repository import ReadStore


def test_gc_repeated_budget_interruptions_keep_a_valid_batch_across_cycles(tmp_path, monkeypatch):
    import sqlite3
    import time

    from doxagent.v2_read import artifact_registry, maintenance

    store = ReadStore(tmp_path / 'read.db')
    limits, cycles = [], []

    class StopCycles(Exception):
        pass

    def interrupted(store, *, limit, **kwargs):
        if not 1 <= limit <= 5000:
            raise ValueError('GC limit must be 1..5000')
        limits.append(limit)
        raise sqlite3.OperationalError('interrupted')

    def next_cycle(seconds):
        cycles.append(seconds)
        if len(cycles) == 10:
            raise StopCycles()

    monkeypatch.setattr(maintenance, 'collect', interrupted)
    monkeypatch.setattr(artifact_registry, 'collect_diagnostics', lambda *a, **kw: [])
    monkeypatch.setattr(time, 'sleep', next_cycle)
    with pytest.raises(StopCycles):
        maintenance.run_gc(store)
    assert len(cycles) == 10 and min(limits) == 10


def test_gc_retains_pinned_snapshot_and_expires_tokens_after_deletion(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    now = datetime.now(UTC)
    row = {"kind": "case", "ticker": "MU", "id": "c", "data": {"n": 1}}
    seq = store.ingest("runtime", "1", [row], at=(now - timedelta(days=5)).isoformat())
    token = store.save_token("owner", "scope", {"seq": seq}, now=now, view=True)
    for n in (2, 3):
        store.ingest(
            "runtime", str(n), [{**row, "data": {"n": n}}], at=(now - timedelta(days=4)).isoformat()
        )
    collect(store, now=now + timedelta(minutes=14))
    assert store.get("case", "MU", "c", seq)["n"] == 1
    assert store.token(token, "owner", view=True, now=now + timedelta(minutes=14))["seq"] == seq
    collect(store, now=now + timedelta(minutes=31))
    with pytest.raises(TimeoutError):
        store.token(token, "owner", view=True, now=now + timedelta(minutes=31))
    assert store.get("case", "MU", "c")["n"] == 3


def test_alias_adopts_new_generation_and_explicitly_resets_old_cursor(tmp_path):
    old, new = ReadStore(tmp_path / "old.db"), ReadStore(tmp_path / "new.db")
    old.migrate()
    new.migrate()
    token = old.save_token("owner", "scope", {"seq": 1})
    alias = tmp_path / "active.json"
    atomic_json(alias, {"format": "doxagent.v2.read-alias.1", "path": str(new.path)})
    active = ReadStore(alias)
    assert active.path == new.path
    with pytest.raises(TimeoutError):
        active.token(token, "owner")


def test_source_gaps_retain_repair_coordinates_without_freezing_unrelated_history(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    now = datetime.now(UTC)
    old = (now - timedelta(days=5)).isoformat()
    for n in range(1, 5):
        store.ingest("bus", str(n), [{"kind": "native:poll_states", "ticker": "MU",
                                      "id": "b", "data": {"n": n}}], at=old)
        store.ingest("runtime", str(n), [{"kind": "case", "ticker": "MU",
                                          "id": "c", "data": {"n": n}}], at=old)
    with store.connect(write=True) as db:
        db.execute("UPDATE commit_retention SET projected_at=?", (old,))
        for coordinate in (1240145, 1240148, 1240191, 1240193):
            db.execute("INSERT INTO gaps(source,event,reason) VALUES('runtime',?,'PROVENANCE_UNVERIFIED')", (str(coordinate),))
    result = collect(store, now=now, budget=1)
    assert not result["protected"]
    assert len(result["repair_scope"]) == 4
    assert result["deleted"]["object_history"] == 6
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM gaps").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM objects WHERE kind='case'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM objects WHERE kind='native:poll_states'").fetchone()[0] == 1
    # A Bus repair is another exact exception, not a global history hold.
    with store.connect(write=True) as db:
        db.execute("INSERT INTO gaps(source,event,reason) VALUES('bus','1','UNKNOWN')")
    assert len(collect(store, now=now, budget=1)["repair_scope"]) == 5
