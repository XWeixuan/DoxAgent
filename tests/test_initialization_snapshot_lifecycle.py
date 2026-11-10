from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.snapshot_retention import collect
from doxagent.codex_runtime.errors import AttemptConflict


def test_snapshots_share_only_immutable_contents_and_forks_are_private(tmp_path):
    store=LocalWorkspaceStore(tmp_path)
    source=store.ensure_run('init-old')/'artifacts'/'script'
    source.write_text('before');source.chmod(0o700)
    store.snapshot('init-old','a');store.snapshot('init-old','b')
    a=store._snapshot_root('init-old','a')/'artifacts'/'script'
    b=store._snapshot_root('init-old','b')/'artifacts'/'script'
    assert os.path.samefile(a,b) and not os.path.samefile(a,source)
    assert a.stat().st_mode&0o777==source.stat().st_mode&0o777
    source.write_text('after')
    store.fork_snapshot('init-old','a','rerun-private')
    fork=store.root/'rerun-private/artifacts/script';fork.write_text('fork')
    assert a.read_text()==b.read_text()=='before'
    store.ensure_run('w3-template-live')
    store.snapshot('w3-template-live','a')
    assert not (store.root/'.init-owners'/(store._snapshot_root('w3-template-live','a').name+'.json')).exists()


def test_terminal_snapshot_exit_preserves_current_previous_and_active_rerun(tmp_path):
    store=LocalWorkspaceStore(tmp_path/'workspaces');jobs=SimpleNamespace(store=JobStore(store.root))
    init=tmp_path/'initialization.db';runtime=tmp_path/'runtime.db'
    now=datetime.now(UTC)
    with sqlite3.connect(init) as db:
        db.executescript('CREATE TABLE initialization_runs(id PRIMARY KEY,ticker,payload); CREATE TABLE initialization_attempts(execution_id PRIMARY KEY,run_id); CREATE TABLE initialization_nodes(run_id,node_key,payload); CREATE TABLE ticker_active_revision(ticker,revision_id); CREATE TABLE activation_revisions(revision_id PRIMARY KEY,base_revision);')
        for i,name in enumerate(['old','pinned','previous','current','running']):
            run='init-'+name;at=(now-timedelta(days=30-i)).isoformat()
            db.execute('INSERT INTO initialization_runs VALUES(?,?,?)',(run,'MU',json.dumps(dict(status='RUNNING' if name=='running' else 'SUCCEEDED',updated_at=at))))
            db.execute('INSERT INTO initialization_attempts VALUES(?,?)',(name,run))
            store.ensure_run(run);store.snapshot(run,name)
        db.execute('INSERT INTO initialization_nodes VALUES(?,?,?)',('init-running','internal.rerun',json.dumps(dict(inputs=dict(_snapshot=dict(workspace_run_id='init-pinned',snapshot_id='pinned'))))))
        db.execute('INSERT INTO ticker_active_revision VALUES(?,?)',('MU','init-current-activation'))
    with sqlite3.connect(runtime) as db:db.execute('CREATE TABLE v2_ticker_control(payload)')
    store.fork_snapshot('init-old','old','rerun-existing')
    settings=SimpleNamespace(ticker_initialization_control_path=init,persistent_runtime_v2_sqlite_path=runtime)
    report=collect(store,jobs,settings,now=now)
    assert report['errors']==[] and report['retired']==[store._snapshot_root('init-old','old').name]
    for name in ['pinned','previous','current','running']:assert store._snapshot_root('init-'+name,name).is_dir()
    with pytest.raises(AttemptConflict,match='RETIRED'):store.snapshot('init-old','old')
    with pytest.raises(AttemptConflict,match='RETIRED'):store.fork_snapshot('init-old','old','rerun-new')
    store.fork_snapshot('init-old','old','rerun-existing')
    assert collect(store,jobs,settings,now=now)['retired']==[]


@pytest.mark.parametrize('status,manual,runtime_pin,rerun_pin',[
    ('RUNNING',False,False,False),('FAILED',True,False,False),
    ('SUCCEEDED',False,True,False),('SUCCEEDED',False,False,True),
    ('SUCCEEDED',False,False,False)])
def test_ops_refresh_ledger_owns_snapshots_and_real_recovery_holds(tmp_path,status,manual,runtime_pin,rerun_pin):
    from doxagent.codex_worker.session_retention import run_held
    now=datetime.now(UTC);at=(now-timedelta(days=3)).isoformat()
    primary=tmp_path/'initialization/control.sqlite3';extra=tmp_path/'ops/ionq-refresh/control.sqlite3'
    schema='CREATE TABLE initialization_runs(id PRIMARY KEY,ticker,payload); CREATE TABLE initialization_attempts(execution_id PRIMARY KEY,run_id); CREATE TABLE initialization_nodes(run_id,node_key,payload); CREATE TABLE ticker_active_revision(ticker,revision_id); CREATE TABLE activation_revisions(revision_id PRIMARY KEY,base_revision);'
    for path in (primary,extra):
        path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:db.executescript(schema)
    parent='init-ionq-'+'a'*32;workspace=parent+'-d3-s0001';execution='original-execution'
    with sqlite3.connect(extra) as db:
        db.execute('INSERT INTO initialization_runs VALUES(?,?,?)',(parent,'IONQ',json.dumps({'status':status,'updated_at':at,'manual_resume_required':manual})))
        db.execute('INSERT INTO initialization_attempts VALUES(?,?)',(execution,parent))
        db.execute('INSERT INTO ticker_active_revision VALUES(?,?)',('IONQ',parent+'-activation'))
    workspaces=LocalWorkspaceStore(tmp_path/'workspaces');jobs=SimpleNamespace(store=JobStore(workspaces.root))
    workspaces.ensure_run(workspace);workspaces.snapshot(workspace,execution)
    target=workspaces._snapshot_root(workspace,execution)
    runtime=tmp_path/'runtime.sqlite3'
    with sqlite3.connect(runtime) as db:
        db.execute('CREATE TABLE v2_ticker_control(payload)')
        if runtime_pin:db.execute('INSERT INTO v2_ticker_control VALUES(?)',(json.dumps({'initialization_id':parent}),))
    if rerun_pin:
        with sqlite3.connect(primary) as db:
            db.execute('INSERT INTO initialization_runs VALUES(?,?,?)',('rerun','IONQ',json.dumps({'status':'RUNNING','updated_at':at})))
            db.execute('INSERT INTO initialization_nodes VALUES(?,?,?)',('rerun','internal.rerun',json.dumps({'inputs':{'_snapshot':{'workspace_run_id':workspace,'snapshot_id':execution}}})))
    settings=SimpleNamespace(ticker_initialization_control_path=primary,persistent_runtime_v2_sqlite_path=runtime)
    # Candidate workspace recovery must also consult the actual ops ledger.
    with sqlite3.connect(runtime) as db:db.execute('CREATE TABLE runtime_tasks(receipt,status)')
    assert run_held(settings,workspace)==(status=='RUNNING' or manual)
    if status=='SUCCEEDED' and not runtime_pin and not rerun_pin:
        preview=collect(workspaces,jobs,settings,now=now,dry_run=True)
        assert preview['retired']==[target.name] and target.exists()
        assert 'initialization_control_path' not in json.loads((workspaces.root/'.init-owners'/(target.name+'.json')).read_text())
    result=collect(workspaces,jobs,settings,now=now)
    assert not result['errors'] and result['unknown']==0
    should_hold=status=='RUNNING' or manual or runtime_pin or rerun_pin
    assert target.exists()==should_hold
    marker=json.loads((workspaces.root/'.init-owners'/(target.name+'.json')).read_text())
    assert marker['initialization_control_path']=='ops/ionq-refresh/control.sqlite3'
    if not should_hold:assert result['retired']==[target.name]
