import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.v2_read.cli import backup
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.transport_archive import archive


def test_packed_transport_preserves_events_coordinates_pins_and_backup(tmp_path):
    path = tmp_path / 'live' / 'bus.db'
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE poll_states(binding_id TEXT PRIMARY KEY,source_id TEXT,ticker TEXT,status TEXT,next_dispatch_at TEXT,data_json TEXT)')
    source = SourceOutbox(path, 'bus')
    source.migrate(('poll_states',))
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    with sqlite3.connect(path) as db:
        for n in range(3):
            value = dict(binding_id=str(n), source_id='source', ticker='MU', status='succeeded')
            db.execute("INSERT INTO poll_states VALUES(?,'source','MU','succeeded','1',?)", (str(n), json.dumps(value)))
        db.execute('UPDATE v2_source_outbox SET recorded_at=?', (old,))
    before = source.read(0)
    checkpoint = tmp_path / 'checkpoint' / 'bus.db'
    backup(path, checkpoint)
    source.acknowledge('reader', 3, repair_pin=2)
    assert archive(source, checkpoint) == 1
    assert source.event(1) == before[0]
    assert source.coordinates(0) == [1, 2, 3]
    source.acknowledge('reader', 3)
    assert archive(source, checkpoint) == 2
    assert source.read(0) == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM v2_source_outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM v2_receipt_packs').fetchone()[0] == 3
    restored = tmp_path / 'restored' / 'bus.db'
    backup(path, restored)
    assert SourceOutbox(restored, 'bus').read(0) == before
    with sqlite3.connect(checkpoint) as db:
        db.execute("UPDATE v2_source_state SET epoch='different'")
    with pytest.raises(ValueError, match='epoch'):
        archive(source, checkpoint)


def test_pack_budget_reduces_batch_without_stalling_on_large_receipts(tmp_path):
    path = tmp_path / 'bus.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE poll_states(binding_id TEXT PRIMARY KEY,data_json TEXT)')
    source = SourceOutbox(path, 'bus')
    source.migrate(('poll_states',))
    with sqlite3.connect(path) as db:
        for n in range(3):
            db.execute('INSERT INTO poll_states VALUES(?,?)', (str(n), json.dumps({'binding_id': str(n)})))
        db.execute('UPDATE v2_source_receipts SET payload=?', (json.dumps({'binding_id':'large','data_json':'x'*(6*1024*1024)}),))
        db.execute('UPDATE v2_source_outbox SET recorded_at=?', ((datetime.now(UTC)-timedelta(days=8)).isoformat(),))
    checkpoint = tmp_path/'checkpoint.db'
    backup(path,checkpoint)
    source.acknowledge('reader',3)
    assert archive(source,checkpoint) == 1
    assert archive(source,checkpoint) == 2
    assert source.coordinates(0) == [1,2,3]
