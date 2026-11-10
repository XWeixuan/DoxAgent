import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from scripts.ops.w3_backlog_recovery_20261110 import prepare,step,HOLD,HOLD_EPOCH


def test_exact_old_cohort_is_held_two_cases_release_and_new_realtime_is_unchanged(tmp_path):
    runtime,jobs,manifest=tmp_path/'runtime.db',tmp_path/'jobs.db',tmp_path/'recovery.json'
    cases=['case_'+str(n).zfill(32) for n in range(4)]
    with sqlite3.connect(runtime) as db:
        db.executescript('''CREATE TABLE runtime_v2_cases(case_id TEXT PRIMARY KEY,created_at TEXT);
            CREATE TABLE runtime_v2_effects(effect_id TEXT PRIMARY KEY,case_id TEXT,effect_type TEXT,status TEXT,
            available_at TEXT,payload_json TEXT);''')
        for i,cid in enumerate(cases):
            stamp='2026-11-10T12:00:00+00:00' if i<3 else '2026-11-10T16:00:00+00:00'
            db.execute('INSERT INTO runtime_v2_cases VALUES(?,?)',(cid,stamp))
            db.execute('INSERT INTO runtime_v2_effects VALUES(?,?,?,?,?,?)',(str(i),cid,'route_to_w3',
                'RUNNING',stamp,json.dumps(dict(case_id=cid,status='RUNNING',lease_token=4,lease_until=stamp,
                    payload={'provider_wait':{'reason':'usage_limit'}},last_error='Codex provider paused'))))
    with sqlite3.connect(jobs) as db:
        db.executescript('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT,request TEXT,payload TEXT);')
        for i,cid in enumerate(cases):
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(str(i),'queued',json.dumps(dict(
                node='persistent_runtime_w3',attempt_id=cid[-24:]+'-trade_decision',prompt='frozen input')),
                json.dumps(dict(request_sha256='original-hash',next_retry_at=0,infra_recovery_count=0))))
    with sqlite3.connect(jobs) as db:
        frozen=db.execute('SELECT id,request FROM jobs').fetchall()
    state=prepare(runtime,jobs,manifest,cutoff='2026-11-10T15:00:00+00:00')
    assert len(state['cases'])==3
    with sqlite3.connect(runtime) as db:
        assert db.execute('SELECT count(*) FROM runtime_v2_effects WHERE available_at=?',(HOLD,)).fetchone()[0]==3
        assert db.execute("SELECT json_extract(payload_json,'$.payload.provider_wait') FROM runtime_v2_effects WHERE effect_id='0'").fetchone()[0] is None
    with sqlite3.connect(jobs) as db:
        assert db.execute("SELECT json_extract(payload,'$.next_retry_at') FROM jobs WHERE id='2'").fetchone()[0]==HOLD_EPOCH
    status=step(state,manifest)
    assert len(status['active'])==2
    with sqlite3.connect(jobs) as db:
        assert db.execute("SELECT json_extract(payload,'$.next_retry_at') FROM jobs WHERE id='0'").fetchone()[0]<time.time()+1
        assert db.execute("SELECT json_extract(payload,'$.next_retry_at') FROM jobs WHERE id='2'").fetchone()[0]==HOLD_EPOCH
        assert db.execute("SELECT json_extract(payload,'$.next_retry_at') FROM jobs WHERE id='3'").fetchone()[0]==0
        assert db.execute('SELECT id,request FROM jobs').fetchall()==frozen
    # No progress, no repeated control-field writes or manifest rewrite.
    progress=manifest.with_suffix('.progress.json');before=progress.stat().st_mtime_ns
    step(state,manifest);assert progress.stat().st_mtime_ns==before
    with sqlite3.connect(runtime) as db:db.execute("UPDATE runtime_v2_effects SET status='COMPLETED' WHERE effect_id='0'")
    with sqlite3.connect(jobs) as db:db.execute("UPDATE jobs SET status='succeeded' WHERE id='0'")
    assert step(state,manifest)['done']==1
    assert len(state['active'])==2 and cases[2] in state['active']


def test_restarted_worker_preserves_explicit_future_job_recovery_time(tmp_path):
    from doxagent.codex_worker.jobs import WorkerJobManager
    from doxagent.codex_worker.job_store import JobStore
    from doxagent.codex_worker.schema import WorkerJob
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from tests.test_worker_resource_budget import request
    manager=object.__new__(WorkerJobManager)
    manager.store=JobStore(tmp_path)
    manager._workspaces=LocalWorkspaceStore(tmp_path)
    manager._jobs=SimpleNamespace()
    class Jobs:
        def __getitem__(self,key):return manager.store.get(key)
        def __setitem__(self,key,job):manager.store.save(job)
    manager._jobs=Jobs()
    manager.store.save(WorkerJob(job_id='original',run_id='run-0',attempt_id='attempt',status='running',
        next_retry_at=HOLD_EPOCH,request_sha256='frozen-hash'),request())
    manager._recover_snapshots()
    recovered=manager.store.get('original')
    assert recovered.status=='queued' and recovered.next_retry_at==HOLD_EPOCH
    assert recovered.request_sha256=='frozen-hash' and recovered.infra_recovery_count==1
