import json
import sqlite3

from doxagent.v2_read.outbox import SourceOutbox


def test_dispatch_only_writes_do_not_capture_but_success_and_errors_do(tmp_path):
    path = tmp_path / "bus.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE poll_states(binding_id TEXT PRIMARY KEY,source_id TEXT,ticker TEXT,status TEXT,next_dispatch_at TEXT,data_json TEXT)")
    source = SourceOutbox(path, "bus")
    source.migrate(("poll_states",))
    value = dict(binding_id="b", source_id="s", ticker="MU", status="succeeded", last_success_at="first")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO poll_states VALUES('b','s','MU','succeeded','1',?)", (json.dumps(value),))
        for n in range(10):
            value.update(next_dispatch_at=str(n), updated_at=str(n), target_due_at=str(n))
            db.execute("UPDATE poll_states SET next_dispatch_at=?,data_json=?", (str(n), json.dumps(value)))
        assert db.execute("SELECT count(*) FROM v2_source_outbox").fetchone()[0] == 1
        value["last_success_at"] = "second"
        db.execute("UPDATE poll_states SET data_json=?", (json.dumps(value),))
        value.update(status="failed", last_error_code="TIMEOUT")
        db.execute("UPDATE poll_states SET status='failed',data_json=?", (json.dumps(value),))
        assert db.execute("SELECT count(*) FROM v2_source_outbox").fetchone()[0] == 3
    assert source.read(0)[-1]["row"]["status"] == "failed"
