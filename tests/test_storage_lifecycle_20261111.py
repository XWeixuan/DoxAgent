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


@pytest.mark.parametrize('child_owner,active_sibling',[(False,False),(True,False),(True,True)])
def test_settled_partial_d3_workspace_exits_after_one_day_but_published_artifact_stays(tmp_path,child_owner,active_sibling):
    import hashlib
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from doxagent.codex_worker.job_store import JobStore
    from doxagent.codex_worker.workspace_retention import collect
    from doxagent.workflows.codex_document3.state_v21 import StateV21,canonical
    now=datetime.now(UTC);at=(now-timedelta(days=2)).isoformat();run='runtime-maintain-test-o3'
    workspaces=LocalWorkspaceStore(tmp_path/'workspaces');jobs=SimpleNamespace(store=JobStore(workspaces.root))
    workspaces.write_text(run,'artifacts/final.md','canonical result')
    child=run+'-s0001' if child_owner else run
    if child_owner:workspaces.write_text(run,'context/context.json','p'*100_000)
    workspaces.write_text(child,'output/final/final.md','canonical result')
    workspaces.write_text(child,'output/final/unique.md','unique unpublished evidence')
    workspaces.write_text(child,'context/context.json','x'*100_000)
    source=tmp_path/'research.db';state=StateV21(source)
    state.start(run,{'ticker':'MU'},{'status':'PARTIAL','phase':'STAGED','prepared':{'files':{'large':'x'*100_000}},'candidate_handoff':{'version':1}})
    state.settle(run,at=at,authority='published-candidate')
    with state.connect() as db:
        db.execute('CREATE TABLE codex_runtime_records(record_type,run_id,payload_json)')
        bundle={'status':'published','published_at':at,'artifacts':[{'relative_path':'artifacts/final.md',
                'sha256':hashlib.sha256(b'canonical result').hexdigest()}]}
        db.execute('INSERT INTO codex_runtime_records VALUES(?,?,?)',('bundles',run,canonical(bundle)))
        db.execute('INSERT INTO codex_document3_v21_staged_sets VALUES(?,?,?,?,?)',('MU',1,run,canonical({'policies':[]}),canonical({'files':{}})))
    with closing(jobs.store.connect()) as db,db:
        db.execute('INSERT INTO jobs(id,status,payload) VALUES(?,?,?)',('job','succeeded',canonical({'run_id':child,'finished_at':at})))
        if active_sibling:db.execute('INSERT INTO jobs(id,status,payload) VALUES(?,?,?)',('active','running',canonical({'run_id':run+'-s0002','updated_at':at})))
    settings=SimpleNamespace(codex_runtime_sqlite_path=source,persistent_runtime_v2_sqlite_path=None,ticker_initialization_control_path=None)
    result=collect(workspaces,jobs,settings,now=now)
    if active_sibling:
        assert not result['runs'] and (workspaces.root/child/'context/context.json').exists()
        assert 'prepared' in state.run(run)
        return
    assert not result['errors'] and result['runs'][0]['retirement_basis']=='settled'
    assert not (workspaces.root/run/'context/context.json').exists()
    assert not (workspaces.root/child/'context/context.json').exists()
    assert (workspaces.root/run/'artifacts/final.md').read_text()=='canonical result'
    assert not (workspaces.root/child/'output/final/final.md').exists()
    assert (workspaces.root/child/'output/final/unique.md').read_text()=='unique unpublished evidence'
    assert 'prepared' not in state.run(run)


def test_small_cold_cache_exits_without_touching_identity(tmp_path,monkeypatch):
    import os,time
    from doxagent.site_strategy.resource_gc import maintain_profile
    monkeypatch.setattr('doxagent.site_strategy.runtime.profile_browser_processes',lambda _:[])
    cache=tmp_path/'identity/Default/Code Cache';cache.mkdir(parents=True)
    old=cache/'old';old.write_text('rebuildable');os.utime(old,(time.time()-8*86400,)*2)
    fresh=cache/'fresh';fresh.write_text('recent')
    cookie=cache.parent/'Cookies';cookie.write_text('identity')
    result=maintain_profile(tmp_path,'identity',allow_cache=True,seconds=2)
    assert result['deleted_bytes']>0 and not old.exists()
    assert fresh.exists() and cookie.read_text()=='identity'


def test_legacy_poll_head_exit_preserves_a_live_mvcc_view(tmp_path,monkeypatch):
    from doxagent.v2_read.repository import ReadStore
    from doxagent.v2_read.serving_gc import collect
    from doxagent.v2_read.serving_fields import select
    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    monkeypatch.delenv('DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED',raising=False)
    now=datetime.now(UTC);store=ReadStore(tmp_path/'read.db');store.migrate()
    old={'dispatch_at':(now-timedelta(days=3)).isoformat(),'status':'FAILED'}
    recent={'dispatch_at':now.isoformat(),'status':'CAPTURED'}
    seq=store.ingest('bus','legacy',[dict(kind='native:poll_runs',ticker='MU',id='old',data=old),
                                    dict(kind='native:poll_runs',ticker='MU',id='recent',data=recent)])
    store.save_token('reader','scope',{'seq':seq},view=True)
    AcquisitionStore(tmp_path/'acquisition.sqlite3',initialize=True)
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH',str(tmp_path/'bus.sqlite3'))
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED','true')
    result=collect(store,now=now,budget=2)
    assert result['deleted']['poll_current']==1
    assert store.get('native:poll_runs','MU','old') is None
    assert store.get('native:poll_runs','MU','old',seq)['status']=='FAILED'
    assert store.get('native:poll_runs','MU','recent')['status']=='CAPTURED'
    assert select('native:poll_runs','future',recent) is None


def test_explicit_d3_exit_does_not_wait_orphan_grace_and_shared_inputs_stay(tmp_path):
    from doxagent.workflows.codex_document3.state_v21 import StateV21,canonical
    from doxagent.v2_read.source_native_gc import collect,migrate
    path=tmp_path/'research.db';state=StateV21(path)
    for number in (1,2):
        run=str(number)
        state.start(run,{'ticker':'MU'},{'prepared':{'files':{'same':'x'*100_000}}})
        with state.connect() as db:
            db.execute('INSERT INTO codex_document3_v21_staged_sets VALUES(?,?,?,?,?)',('MU',number,run,canonical({'policies':[]}),canonical({'files':{}})))
    with state.connect() as db:
        db.execute('CREATE TABLE v2_source_consumers(id TEXT PRIMARY KEY,updated_at TEXT)')
        migrate(db);db.execute("INSERT INTO v2_native_ref_scan VALUES('fixture-complete',0,1)")
    source=SimpleNamespace(path=path,connection=lambda write=False:sqlite3.connect(path))
    state.retire('1',now=datetime.now(UTC),authority='published')
    assert collect(source)['quarantined']==0
    assert state.run('2')['prepared']['files']['same']=='x'*100_000
    state.retire('2',now=datetime.now(UTC),authority='published')
    assert collect(source)['quarantined']==1


def test_d3_input_format_has_a_reversible_old_reader_path(tmp_path):
    from doxagent.workflows.codex_document3.state_v21 import StateV21,inline_for_rollback
    path=tmp_path/'research.db';state=StateV21(path);value={'prepared':{'files':{'f':'x'*50_000}}}
    state.start('run',{},value)
    assert inline_for_rollback(path)['rewritten']==1
    with state.connect() as db:assert json.loads(db.execute('SELECT payload FROM codex_document3_v21_runs').fetchone()[0])==value
    state.start('during-rollout',{},value)
    with state.connect() as db:assert json.loads(db.execute("SELECT payload FROM codex_document3_v21_runs WHERE run_id='during-rollout'").fetchone()[0])==value


def test_completed_maintenance_frame_keeps_frozen_selection_and_canonical_revision(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal,encode
    from doxagent.persistent_runtime_v2.resource_gc import compact_frame
    now=datetime.now(UTC);at=(now-timedelta(days=2)).isoformat();journal=RuntimeJournal(tmp_path/'runtime.db')
    run='runtime-maintain-one';journal.put_task('task','MU','MAINTENANCE',{'cutoff':at})
    base={'revision_id':'base','artifacts':{'event_library':{'root':'/events','version':1}},'canonical':'x'*100_000}
    frame={'run_id':run,'base':base,'case_ids':[],'effect_versions':{},'policy_consumption':[{'policy_id':'P1','generation':1}]}
    receipt=journal.content.encode({'frame':frame,'o3':{'version':2}})
    with journal.transaction() as db:
        db.execute("UPDATE runtime_tasks SET status='SUCCEEDED',updated_at=?,receipt=? WHERE id='task'",(at,receipt))
    control=tmp_path/'control.db'
    with sqlite3.connect(control) as db:
        db.execute('CREATE TABLE activation_revisions(revision_id PRIMARY KEY,ticker,payload)')
        db.executemany('INSERT INTO activation_revisions VALUES(?,?,?)',[('base','MU',encode(base)),(run+'-activation','MU','{}')])
    proof=compact_frame(journal,control,run,{'task':'task','status':'SUCCEEDED','at':at},now)
    assert proof['frame_payload_bytes']>100_000
    with journal.transaction(write=False) as db:value=json.loads(db.execute("SELECT receipt FROM runtime_tasks WHERE id='task'").fetchone()[0])
    assert 'frame' not in value and value['o3']=={'version':2}
    assert value['retired_frame']['frozen_selection']['policy_consumption']==frame['policy_consumption']
    assert value['retired_frame']['base_revision']=='base' and not value['retired_frame']['resume_allowed']
    assert not compact_frame(journal,control,run,{'task':'task','status':'SUCCEEDED','at':at},now)


def test_runtime_capture_leaves_process_payload_at_owner_and_keeps_progress(tmp_path):
    from doxagent.v2_read.outbox import SourceOutbox
    path=tmp_path/'runtime.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runtime_tasks(id TEXT PRIMARY KEY,ticker TEXT,status TEXT,inputs TEXT,receipt TEXT)')
        db.execute("CREATE TRIGGER v2_capture_runtime_tasks_INSERT AFTER INSERT ON runtime_tasks BEGIN SELECT json_object('inputs',NEW.inputs,'receipt',NEW.receipt); END")
    source=SourceOutbox(path,'runtime');source.migrate(('runtime_tasks',))
    inputs=json.dumps({'files':{'full':'x'*100_000}})
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO runtime_tasks VALUES(?,?,?,?,?)',('task','MU','RUNNING',inputs,'{}'))
        db.execute("UPDATE runtime_tasks SET status='SUCCEEDED',receipt=? WHERE id='task'",(inputs,))
        packets=[json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_source_receipts ORDER BY id')]
        assert all('inputs' not in p and 'receipt' not in p for p in packets)
        assert [p['status'] for p in packets]==['RUNNING','SUCCEEDED']
        assert db.execute("SELECT inputs,receipt FROM runtime_tasks WHERE id='task'").fetchone()==(inputs,inputs)
    assert source.event(2)['row']['status']=='SUCCEEDED'
    assert 'inputs' not in source.event(2)['row']


def test_poll_current_candidate_cursor_advances_past_recent_protected_prefix(tmp_path,monkeypatch):
    import time
    from doxagent.v2_read.repository import ReadStore
    from doxagent.v2_read.serving_gc import _poll_current
    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH',str(tmp_path/'bus.sqlite3'))
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED','false')
    AcquisitionStore(tmp_path/'acquisition.sqlite3',initialize=True)
    store=ReadStore(tmp_path/'read.db');store.migrate();now=datetime.now(UTC)
    store.ingest('fixture','1',[dict(kind='native:poll_runs',ticker='MU',id=f'{i:03}',
        data={'dispatch_at':now.isoformat()}) for i in range(25)]+[
        dict(kind='native:poll_runs',ticker='MU',id='old-z',data={'dispatch_at':(now-timedelta(days=3)).isoformat()})])
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED','true')
    first,seen=_poll_current(store,now,4,time.monotonic()+1)
    assert seen==4 and first['poll_current']==0
    for _ in range(7):_poll_current(store,now,4,time.monotonic()+1)
    assert store.get('native:poll_runs','MU','old-z') is None
    assert store.get('native:poll_runs','MU','000') is not None


def test_initialization_child_workspace_honors_parent_manual_recovery(tmp_path):
    from doxagent.codex_worker.session_retention import run_held
    parent='init-mu-'+'a'*32;path=tmp_path/'control.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE initialization_runs(id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('INSERT INTO initialization_runs VALUES(?,?)',(parent,json.dumps({'status':'FAILED','manual_resume_required':True})))
    settings=SimpleNamespace(ticker_initialization_control_path=path,persistent_runtime_v2_sqlite_path=None)
    assert run_held(settings,parent+'-d3-s0001')


@pytest.mark.parametrize('status',['SUCCEEDED','RUNNING'])
def test_runtime_o2_partial_workspace_inherits_real_parent_settlement(tmp_path,status):
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from doxagent.codex_worker.job_store import JobStore
    from doxagent.codex_worker.workspace_retention import collect
    now=datetime.now(UTC);at=(now-timedelta(days=2)).isoformat();parent='runtime-maintain-'+'a'*24;child=parent+'-o2'
    source=tmp_path/'research.db';runtime=tmp_path/'runtime.db'
    with sqlite3.connect(source) as db:db.execute('CREATE TABLE codex_runtime_records(record_type,run_id,payload_json)')
    with sqlite3.connect(runtime) as db:
        db.execute('CREATE TABLE runtime_tasks(status,updated_at,receipt)')
        db.execute('INSERT INTO runtime_tasks VALUES(?,?,?)',(status,at,json.dumps({'_gc_owner':{'run_id':parent}})))
    workspaces=LocalWorkspaceStore(tmp_path/'workspaces');jobs=SimpleNamespace(store=JobStore(workspaces.root))
    workspaces.write_text(child,'context/context.json','x'*100_000)
    with closing(jobs.store.connect()) as db,db:db.execute('INSERT INTO jobs(id,status,payload) VALUES(?,?,?)',('partial','failed',json.dumps({'run_id':child,'finished_at':at})))
    settings=SimpleNamespace(codex_runtime_sqlite_path=source,persistent_runtime_v2_sqlite_path=runtime,ticker_initialization_control_path=None)
    result=collect(workspaces,jobs,settings,now=now)
    assert not result['errors']
    assert (workspaces.root/child/'context/context.json').exists()==(status=='RUNNING')
    if status=='SUCCEEDED':assert result['runs'][0]['retirement_basis']=='settled'
