"""Production failure mechanisms: scale, real commits, and business terminal ownership."""
from contextlib import closing,contextmanager
from datetime import UTC,datetime,timedelta
import json
import sqlite3
from types import SimpleNamespace

import pytest


def test_read_floor_uses_time_index_under_large_old_ledger(tmp_path):
    from doxagent.v2_read.serving_gc import FLOOR_SQL
    with sqlite3.connect(tmp_path/'floor.db') as db:
        db.executescript('CREATE TABLE commit_retention(seq INTEGER PRIMARY KEY,projected_at TEXT);'
                         'CREATE INDEX commit_retention_at ON commit_retention(projected_at,seq);')
        db.executemany('INSERT INTO commit_retention VALUES(?,?)',((i,'2000-01-01') for i in range(1,1_000_001)))
        db.execute("INSERT INTO commit_retention VALUES(1000001,'2099-01-01')")
        steps=0
        def check():
            nonlocal steps
            steps+=1
            return steps>10
        db.set_progress_handler(check,1000)
        assert db.execute(FLOOR_SQL,('2090-01-01',)).fetchone()[0]==1_000_001
        assert any('commit_retention_at' in r[3] for r in db.execute('EXPLAIN QUERY PLAN '+FLOOR_SQL,('2090',)))


def test_read_later_stage_failure_does_not_undo_committed_history_exit(tmp_path):
    from doxagent.v2_read.repository import ReadStore
    from doxagent.v2_read.serving_gc import collect
    store=ReadStore(tmp_path/'read.db');store.migrate()
    for i in range(2):store.ingest('s',str(i),[dict(kind='case',ticker='MU',id='case',data={'status':str(i)})])
    with store.connect(write=True) as db:
        db.execute("INSERT INTO content_search VALUES('orphan','body')")
        db.execute("CREATE TRIGGER refuse_search BEFORE DELETE ON content_search BEGIN SELECT RAISE(ABORT,'fixture fault'); END")
    now=datetime.now(UTC)+timedelta(hours=2)
    with pytest.raises(sqlite3.IntegrityError,match='fixture fault'):collect(store,now=now,budget=2)
    with store.connect(write=True) as db:
        assert db.execute('SELECT count(*) FROM object_history').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM object_current').fetchone()[0]==1
        db.execute('DROP TRIGGER refuse_search')
    assert collect(store,now=now,budget=2)['deleted']['content_search']==1


def test_physical_candidates_bounded_and_progress_when_everything_is_referenced(tmp_path):
    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    from doxagent.message_bus_v2.collection_plane.maintenance import _physical
    store=AcquisitionStore(tmp_path/'acquisition.db',initialize=True)
    at='2026-10-01T00:00:00+00:00'
    with store.connect(write=True) as db:
        db.executemany('INSERT INTO physical_cycles VALUES(?,?,?)',((f'p{i:07}',at,'[]') for i in range(100_000)))
        db.executemany('INSERT INTO cycles(run_id,data_json) VALUES(?,?)',((f'p{i:07}:binding','{}') for i in range(100_000)))
    original=store.connect;steps=0
    @contextmanager
    def limited(**kwargs):
        with original(**kwargs) as db:
            def count():
                nonlocal steps
                steps+=1
                return steps>5000
            db.set_progress_handler(count,100)
            yield db
    store.connect=limited
    assert _physical(store,'2026-11-01')['scanned']==512
    assert store.get_meta('physical_exit_cursor')[1]=='p0000511'
    assert _physical(store,'2026-11-01')['deleted']==0
    assert store.get_meta('physical_exit_cursor')[1]=='p0001023'


def test_hour_commit_survives_other_retirement_failure(tmp_path,monkeypatch):
    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    from doxagent.message_bus_v2.collection_plane import maintenance as m
    store=AcquisitionStore(tmp_path/'acquisition.db',initialize=True)
    at=datetime(2026,10,1,tzinfo=UTC).isoformat()
    with store.connect(write=True) as db:
        db.execute('INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('p:b','b','s','MU','HEAD',at,at,60,1,1,'FAILED','refused',at,'{}'))
    monkeypatch.setattr(m,'utc_now',lambda:datetime(2026,10,4,tzinfo=UTC))
    def broken(*a):raise sqlite3.OperationalError('fixture physical failure')
    monkeypatch.setattr(m,'_physical',broken)
    result=m.maintain(store,hours=1)
    assert result['errors'][0]['stage']=='physical'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM unit_hours').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM cycles').fetchone()[0]==0


def test_d3_immutable_inputs_and_compaction_preserve_exact_published_version(tmp_path):
    from doxagent.workflows.codex_document3.state_v21 import StateV21,canonical
    state=StateV21(tmp_path/'research.db')
    value={'identity':{'ticker':'MU'},'status':'PARTIAL','phase':'STAGED',
           'prepared':{'files':{'source':'x'*200_000}},'final':[{'id':'P1'}],
           'coverage':{'missing':['not complete']},'accepted_rearm':['P1']}
    state.start('run',{'ticker':'MU'},value)
    with state.connect() as db:
        stored=json.loads(db.execute("SELECT payload FROM codex_document3_v21_runs").fetchone()[0])
        original=stored['prepared']
        assert '$doxagent_content_v1' in original
        db.execute('INSERT INTO codex_document3_v21_staged_sets VALUES(?,?,?,?,?)',
                   ('MU',1,'run',canonical({'policies':[{'id':'P1'}]}),canonical({'files':{'document3':'published.json'}})))
    state.update('run',status='PARTIAL')
    assert state.run('run')['prepared']==value['prepared']
    with state.connect() as db:
        assert json.loads(db.execute("SELECT payload FROM codex_document3_v21_runs").fetchone()[0])['prepared']==original
    state.settle('run',authority='published-candidate')
    receipt=state.retire('run',now=datetime.now(UTC),authority='published-candidate')
    assert not receipt['resume_allowed']
    retired=state.run('run')
    assert retired['final']==[{'id':'P1'}] and retired['accepted_rearm']==['P1']
    assert retired['coverage']==value['coverage'] and 'prepared' not in retired
    with pytest.raises(ValueError,match='D3_RUN_RETIRED'):state.start('run',{'ticker':'MU'},value)


def test_operation_snapshot_reuses_attempt_and_keeps_only_verified_successor(tmp_path):
    from doxagent.v2_read.operation_backup import snapshot,finish
    source=tmp_path/'source.db';root=tmp_path/'backups'
    with sqlite3.connect(source) as db:db.execute('CREATE TABLE identity(id PRIMARY KEY)');db.execute('INSERT INTO identity VALUES(1)')
    first=snapshot(source,root,family='registry',operation='one')
    assert snapshot(source,root,family='registry',operation='one')==first
    finish(first,success=True)
    second=snapshot(source,root,family='registry',operation='two')
    assert first.exists() and second.exists()
    finish(second,success=True)
    assert not first.exists() and second.exists()
    with closing(sqlite3.connect(second)) as db:assert db.execute('SELECT id FROM identity').fetchone()[0]==1
