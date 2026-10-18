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
        for run in ('published','held','w3-template-old','corrupt'):
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
    assert [r['run'] for r in report['runs']]==['published']
    assert report['errors'][0]['run']=='corrupt'
    assert not (workspaces.root/'published/context/reproducible.txt').exists()
    assert (workspaces.root/'published/context/source.html').read_text()=='unique cited source'
    assert (workspaces.root/'published/attempts/a/input/observations.sqlite3').is_file()
    for run in ('held','w3-template-old','corrupt'):assert (workspaces.root/run/'context/reproducible.txt').exists()
    assert not retention.collect(workspaces,SimpleNamespace(store=store),SimpleNamespace(codex_runtime_sqlite_path=source),now=now)['runs']
