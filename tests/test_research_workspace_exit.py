from datetime import UTC,datetime,timedelta
import hashlib
import json
import sqlite3
from types import SimpleNamespace

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.codex_worker import workspace_retention as retention


def test_published_inputs_exit_but_adopted_and_unique_evidence_and_w3_stay(tmp_path,monkeypatch):
    now=datetime.now(UTC);at=(now-timedelta(days=30)).isoformat()
    workspaces=LocalWorkspaceStore(tmp_path/'workspaces');store=JobStore(workspaces.root)
    source=tmp_path/'research.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE codex_runtime_records(record_type,run_id,payload_json)')
        for run in ('published','runtime-maintain-old','held','w3-template-old','corrupt'):
            workspaces.write_text(run,'artifacts/final.md','result')
            workspaces.write_text(run,'context/reproducible.txt','old input copy')
            workspaces.write_text(run,'context/source.html','unique cited source')
            workspaces.write_text(run,'attempts/a/input/payload.txt','old input')
            workspaces.write_text(run,'attempts/a/input/observations.sqlite3','unique evidence')
            artifact=dict(relative_path='artifacts/final.md',sha256=hashlib.sha256(b'result').hexdigest())
            if run=='corrupt':artifact['sha256']='bad'
            db.execute('INSERT INTO codex_runtime_records VALUES(?,?,?)',('bundles',run,json.dumps(dict(status='published',artifacts=[artifact]))))
            db.execute('INSERT INTO codex_runtime_records VALUES(?,?,?)',('citations',run,json.dumps(dict(relative_path='context/source.html'))))
            with store.connect() as jobs:
                jobs.execute('INSERT INTO jobs(id,status,payload,request) VALUES(?,?,?,?)',(run,'succeeded',json.dumps(dict(run_id=run,finished_at=at)),json.dumps(dict(node='document3'))))
    monkeypatch.setattr(retention,'run_held',lambda settings,run:run=='held')
    report=retention.collect(workspaces,SimpleNamespace(store=store),SimpleNamespace(codex_runtime_sqlite_path=source),now=now)
    assert [r['run'] for r in report['runs']]==['published','runtime-maintain-old']
    assert report['errors'][0]['run']=='corrupt'
    assert not (workspaces.root/'published/context/reproducible.txt').exists()
    assert (workspaces.root/'published/context/source.html').read_text()=='unique cited source'
    assert (workspaces.root/'published/attempts/a/input/observations.sqlite3').is_file()
    for run in ('held','w3-template-old','corrupt'):assert (workspaces.root/run/'context/reproducible.txt').exists()
    assert not retention.collect(workspaces,SimpleNamespace(store=store),SimpleNamespace(codex_runtime_sqlite_path=source),now=now)['runs']


def test_unpublished_terminal_inputs_exit_without_losing_results_or_resumable_runs(tmp_path,monkeypatch):
    now=datetime.now(UTC);old=(now-timedelta(days=30)).isoformat();fresh=(now-timedelta(days=10)).isoformat()
    workspaces=LocalWorkspaceStore(tmp_path/'workspaces');store=JobStore(workspaces.root);source=tmp_path/'research.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE codex_runtime_records(record_type,run_id,payload_json)')
        for run,status,at,node in [('failure','failed',old,'document3'),('cancelled','cancelled',old,'document3'),('success','succeeded',old,'document3'),('recent-failure','failed',fresh,'document3'),('held','failed',old,'document3'),('active','running',old,'document3'),('w3-job','failed',old,'persistent_runtime_w3')]:
            for name in ['context/copied.txt','context/cited.html','attempts/a/input/copied.txt','attempts/a/input/observations.sqlite3','artifacts/unique-draft.md','attempts/a/output/result.json','audit/error.json']:
                workspaces.write_text(run,name,'retained or reproducible')
            db.execute('INSERT INTO codex_runtime_records VALUES(?,?,?)',('citations',run,json.dumps(dict(relative_path='context/cited.html'))))
            with store.connect() as jobs:
                jobs.execute('INSERT INTO jobs(id,status,payload,request) VALUES(?,?,?,?)',(run,status,json.dumps(dict(run_id=run,finished_at=at)),json.dumps(dict(node=node))))
    monkeypatch.setattr(retention,'run_held',lambda settings,run:run=='held')
    settings=SimpleNamespace(codex_runtime_sqlite_path=source);jobs=SimpleNamespace(store=store)
    report=retention.collect(workspaces,jobs,settings,now=now)
    assert not report['errors']
    assert {r['run'] for r in report['runs']}=={'failure','cancelled','success'}
    assert all(r['retirement_basis']=='terminal-unpublished-inputs' for r in report['runs'])
    for run in ('failure','cancelled','success'):
        assert not (workspaces.root/run/'context/copied.txt').exists()
        assert not (workspaces.root/run/'attempts/a/input/copied.txt').exists()
        for name in ['context/cited.html','attempts/a/input/observations.sqlite3','artifacts/unique-draft.md','attempts/a/output/result.json','audit/error.json']:
            assert (workspaces.root/run/name).is_file()
    for run in ('recent-failure','held','active','w3-job'):
        assert (workspaces.root/run/'context/copied.txt').is_file()
    assert not retention.collect(workspaces,jobs,settings,now=now)['runs']


def test_scoped_d3_parent_uses_typed_semantics_and_never_reads_foreign_owner(tmp_path,monkeypatch):
    from doxagent.workflows.codex_document3.state_v21 import StateV21
    from doxagent.workflows.codex_document3.schema_v21 import PolicySetV3
    from tests.test_runtime_policy_direction_usage import policy
    now=datetime.now(UTC);at=(now-timedelta(days=30)).isoformat()
    source=tmp_path/'research.db';state=StateV21(source)
    workspaces=LocalWorkspaceStore(tmp_path/'workspaces');store=JobStore(workspaces.root)
    parent='d3-parent';child=parent+'-research'
    state.start(parent,{},dict(prepared={'large':'repeat'*1000}))
    state.save_staged_v3(parent,PolicySetV3(ticker='MU',policy_set_version=1,as_of=now,published_at=now,publication_state='COMPLETE',policies=[policy()]),{'files':{'document3.json':'derived'}})
    state.settle(parent,at=at,authority='test-publication')
    for run in (parent,child):
        workspaces.write_text(run,'context/copied.txt','redundant')
        workspaces.write_text(run,'context/cited.html','evidence')
    workspaces.write_text(parent,'artifacts/document3.json','different but regenerable representation')
    workspaces.write_text(parent,'output/final/document3.json','obsolete export')
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE codex_runtime_records(record_type,run_id,payload_json)')
        db.execute('INSERT INTO codex_runtime_records VALUES(?,?,?)',('bundles',parent,json.dumps(dict(status='published',published_at=at,handoff={'policy_set_version':1},artifacts=[dict(relative_path='artifacts/document3.json',sha256='old-byte-digest')]))))
        db.execute('INSERT INTO codex_runtime_records VALUES(?,?,?)',('citations',parent,json.dumps(dict(relative_path='context/cited.html'))))
        # Decoding an unrelated/missing Native object would fail this collection.
        db.execute('INSERT INTO codex_runtime_records VALUES(?,?,?)',('bundles','foreign',json.dumps({'status':'published','unused':{'$doxagent_content_v1':'f'*64}})))
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,status,payload,request) VALUES(?,?,?,?)',(child,'succeeded',json.dumps(dict(run_id=child,finished_at=at)),json.dumps(dict(node='document3'))))
    monkeypatch.setattr(retention,'run_held',lambda *args:False)
    monkeypatch.setattr('doxagent.persistent_runtime_v2.resource_gc.opened_below',lambda *args:(_ for _ in ()).throw(AssertionError('global /proc scan')))
    settings=SimpleNamespace(codex_runtime_sqlite_path=source)
    first=retention.collect(workspaces,SimpleNamespace(store=store),settings,now=now,limit=1,run_ids=[parent])
    assert not first['errors'] and first['runs'][0]['run']==parent
    assert not (workspaces.root/parent/'context/copied.txt').exists()
    assert (workspaces.root/parent/'context/cited.html').is_file()
    assert not (workspaces.root/parent/'output/final/document3.json').exists()
    assert (workspaces.root/parent/'artifacts/document3.json').is_file()


def test_d3_exit_never_retires_state_when_workspace_owner_is_held(tmp_path,monkeypatch):
    from doxagent.workflows.codex_document3.state_v21 import StateV21
    from doxagent.workflows.codex_document3.process_exit import collect
    now=datetime.now(UTC)+timedelta(days=40);at=(now-timedelta(days=40)).isoformat()
    source=tmp_path/'research.db';state=StateV21(source);(tmp_path/'.d3-shared-policy-ready').touch()
    state.start('held',{},dict(prepared={'secret':'necessary until retirement boundary'}))
    workspaces=LocalWorkspaceStore(tmp_path/'workspaces');store=JobStore(workspaces.root)
    workspaces.write_text('held','context/input.txt','not yet eligible')
    with sqlite3.connect(source) as db:db.execute('CREATE TABLE codex_runtime_records(record_type,run_id,payload_json)')
    with store.connect() as db:db.execute('INSERT INTO jobs(id,status,payload,request) VALUES(?,?,?,?)',('j','failed',json.dumps(dict(run_id='held',finished_at=at)),json.dumps(dict(node='document3'))))
    monkeypatch.setattr(retention,'run_held',lambda *args:True)
    report=collect(SimpleNamespace(codex_runtime_sqlite_path=source,codex_workspace_root=workspaces.root),now=now)
    assert 'eligible retirement boundary' in report['error']
    assert 'prepared' in state.run('held')
    assert (workspaces.root/'held/context/input.txt').is_file()
