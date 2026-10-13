import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('cold_archive', Path(__file__).parents[1] / 'scripts/ops/distribution_cold_archive.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_offhost_receipt_and_exact_original_required_before_retirement(tmp_path):
    database = tmp_path / 'bus.db'
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE v2_source_state(source TEXT,epoch TEXT,head INTEGER,retained_floor INTEGER);"
                         "INSERT INTO v2_source_state VALUES('bus','epoch',100,0);"
                         "CREATE TABLE distribution_runs(run_id TEXT PRIMARY KEY,mode TEXT);"
                         "INSERT INTO distribution_runs VALUES('r','REALTIME'),('s','CLOSED_SWEEP');"
                         "CREATE TABLE distribution_deliveries(delivery_id TEXT,run_id TEXT,state TEXT,created_at TEXT);")
        db.executemany('INSERT INTO distribution_deliveries VALUES(?,?,?,?)', [
            ('reject', 'r', 'ADMISSION_REJECTED', old),
            ('changed', 'r', 'ADMISSION_REJECTED', old),
            ('published', 'r', 'PUBLISHED', old),
            ('sweep', 's', 'ADMISSION_REJECTED', old),
            ('pending', 'r', 'PENDING', old),
        ])
    result = module.export(database, tmp_path / 'archive', before=(datetime.now(UTC) - timedelta(days=2)).isoformat())
    assert result['rows'] == 2
    manifest = next((tmp_path / 'archive').glob('*.manifest.json'))
    ack = tmp_path / 'ack.json'
    ack.write_text('{}')
    with pytest.raises(ValueError, match='offhost'):
        module.retire(database, manifest, ack)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM distribution_deliveries').fetchone()[0] == 5
        db.execute("UPDATE distribution_deliveries SET state='PENDING' WHERE delivery_id='changed'")
    ack.write_text(json.dumps(dict(sha256=result['sha256'], bytes=result['bytes'], verified=True,
                                  offhost_path='D:/verified/archive.gz')))
    report = module.retire(database, manifest, ack, batch=1)
    assert report['deleted'] == 1 and report['changed_preserved'] == 1
    with sqlite3.connect(database) as db:
        assert {r[0] for r in db.execute('SELECT delivery_id FROM distribution_deliveries')} == {'changed', 'published', 'sweep', 'pending'}
    assert module.retire(database, manifest, ack)['deleted'] == 0
