import json
import sqlite3
import pytest
from scripts.ops.o3_cutover_fence import apply


def test_cutover_only_rejects_new_runtime_o3_and_allows_receipt_updates(tmp_path):
    path=tmp_path/'jobs.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,priority INTEGER,request TEXT)')
        old=json.dumps({'node': 'd3_o3_maintain', 'run_id': 'runtime-maintain-abc-o3'})
        db.execute('INSERT INTO jobs VALUES(?,0,?)', ('old',old))
    apply(path)
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match='O3_PAUSED'):
            db.execute('INSERT INTO jobs VALUES(?,0,?)', ('new',old))
        with pytest.raises(sqlite3.IntegrityError, match='O3_PAUSED'):
            db.execute('INSERT INTO jobs VALUES(?,0,?)', ('v21',json.dumps({'node':'d3_shell_scan','run_id':'runtime-maintain-abc-o3-shell'})))
        db.execute('INSERT INTO jobs VALUES(?,0,?) ON CONFLICT(id) DO UPDATE SET request=excluded.request', ('old',old))
        for key,priority,node,run in [('o2',0,'o2_maintain','runtime-maintain-abc-o2'),
                                      ('w3',1,'persistent_runtime_w3','w3-case-abc'),
                                      ('init',0,'d3_o3_maintain','initialization-abc')]:
            db.execute('INSERT INTO jobs VALUES(?,?,?)', (key,priority,json.dumps({'node':node,'run_id':run})))
    assert apply(path,remove=True)=={'fence_present':False}
