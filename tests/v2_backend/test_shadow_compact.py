import json
import sqlite3
from datetime import UTC, datetime, timedelta

from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.shadow_compact import build


def test_streaming_shadow_preserves_ids_tokens_gaps_and_native_content(tmp_path):
    source = ReadStore(tmp_path / 'read.db')
    source.migrate()
    source.ingest('bus', '1', [{'kind':'native:poll_states','ticker':'MU','id':'poll','data':{'status':'old'}}])
    source.ingest('bus', '2', [{'kind':'native:poll_states','ticker':'MU','id':'poll','data':{'status':'new'}}])
    source.ingest('runtime', '3', [{'kind':'case','ticker':'MU','id':'case','data':{'detail':'x'*20000}}])
    old = (datetime.now(UTC)-timedelta(days=2)).isoformat().replace('+00:00','Z')
    with sqlite3.connect(source.path) as db:
        db.execute('UPDATE commit_retention SET projected_at=? WHERE seq<=2', (old,))
        db.execute("INSERT INTO gaps(source,event,reason) VALUES('runtime','1240145','PROVENANCE_UNVERIFIED')")
        db.execute("INSERT INTO views VALUES('view','owner',2,'scope','{}','2999-01-01T00:00:00Z')")
        generation = db.execute('SELECT id FROM generation').fetchone()[0]
        db.execute("UPDATE sqlite_sequence SET seq=20 WHERE name='commits'")
        db.execute("INSERT INTO contributions(metric,ticker,entity,day,dimensions,value,valid_from) VALUES('cost','MU','e','2026-10-13','{\"scope\":\"research\"}','1',3)")
    target = tmp_path / 'shadow.db'
    report = build(source.path, target)
    assert report['tables']['objects']['omitted'] == 0  # token at seq=2 needs the pre-image
    assert ReadStore(target).get('case','MU','case') == source.get('case','MU','case')
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT id FROM generation').fetchone()[0] == generation
        assert db.execute('SELECT seq FROM views').fetchone()[0] == 2
        assert db.execute('SELECT event FROM gaps').fetchone()[0] == '1240145'
        assert db.execute('SELECT max(seq) FROM commits').fetchone()[0] == 3
        assert db.execute("SELECT seq FROM sqlite_sequence WHERE name='commits'").fetchone()[0] == 20
        assert db.execute('SELECT scope FROM contributions').fetchone()[0] == 'research'
    with sqlite3.connect(source.path) as db:
        db.execute('DELETE FROM views')
    second = build(source.path, tmp_path/'shadow2.db')
    assert second['tables']['objects']['omitted'] == 1
