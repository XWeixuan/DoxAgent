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
    monkeypatch.setattr(retention,'business_holds',lambda *a,**kw:{('run','held')})
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
    monkeypatch.setattr(retention,'business_holds',lambda *a,**kw:{('run','held')})
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
