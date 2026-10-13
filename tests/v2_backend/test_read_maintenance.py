from datetime import UTC, datetime, timedelta

import pytest

from doxagent.v2_read.maintenance import atomic_json, collect
from doxagent.v2_read.repository import ReadStore


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
    collect(store, now=now + timedelta(hours=23))
    assert store.get("case", "MU", "c", seq)["n"] == 1
    assert store.token(token, "owner", view=True, now=now + timedelta(hours=23))["seq"] == seq
    collect(store, now=now + timedelta(hours=25))
    with pytest.raises(TimeoutError):
        store.token(token, "owner", view=True, now=now + timedelta(hours=25))
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


def test_runtime_gaps_preserve_business_but_do_not_pin_expired_bus_poll_versions(tmp_path):
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
    assert result["policy"] == "BUS_POLL_ONLY"
    assert result["deleted"]["objects"] == 3
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM gaps").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM objects WHERE kind='case'").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM objects WHERE kind='native:poll_states'").fetchone()[0] == 1
    # A Bus repair protects those versions too.
    with store.connect(write=True) as db:
        db.execute("INSERT INTO gaps(source,event,reason) VALUES('bus','1','UNKNOWN')")
    assert collect(store, now=now, budget=1)["policy"] == "PINNED"
