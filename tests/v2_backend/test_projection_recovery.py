import errno
import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from doxagent.v2_read import cli
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.repository import ReadStore


def full():
    e=sqlite3.OperationalError('database or disk is full')
    e.sqlite_errorcode=sqlite3.SQLITE_FULL
    return e


@pytest.mark.parametrize('failure',[full,lambda: OSError(errno.ENOSPC,'full'),lambda: sqlite3.OperationalError('invalid SQL')])
def test_infrastructure_failure_never_skips_coordinate(tmp_path,failure):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    def mapper(event):raise failure()
    worker=ProjectionWorker(store,[],mapper)
    with pytest.raises((sqlite3.OperationalError,OSError)):
        worker._events(SimpleNamespace(source='runtime'),[{'seq':1,'recorded_at':None}],None,budget=1)
    assert store.position('runtime')==0
    with store.connect() as db:assert db.execute('SELECT count(*) FROM gaps').fetchone()[0]==0


def test_repair_storage_failure_does_not_accrue_business_backoff(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    store.ingest('runtime','1',[],position=1,gap='ValueError')
    source=SimpleNamespace(source='runtime',event=lambda n: {'seq':n,'recorded_at':None})
    def mapper(event):raise full()
    worker=ProjectionWorker(store,[source],mapper)
    with pytest.raises(sqlite3.OperationalError):worker.repair()
    with store.connect() as db:assert tuple(db.execute('SELECT attempts,next_attempt_at FROM gaps').fetchone())==(0,'')
    worker.mapper=lambda event: ([{'kind':'fixture','ticker':'','id':'parent','data':{'ok':True}}],[])
    assert worker.repair()==1
    assert store.position('runtime')==1
    with store.connect() as db:assert db.execute('SELECT count(*) FROM gaps').fetchone()[0]==0


def test_full_recovers_in_same_cli_loop(tmp_path,monkeypatch):
    calls=[];delays=[]
    def tick(self,**kwargs):
        calls.append(1)
        if len(calls)<4:raise full()
        raise SystemExit(0)
    monkeypatch.setattr(cli.ProjectionWorker,'tick',tick)
    monkeypatch.setattr(cli.time,'sleep',delays.append)
    monkeypatch.setattr(sys,'argv',['projector','project','--read-db',str(tmp_path/'read.db')])
    with pytest.raises(SystemExit):cli.main()
    assert delays==[1,2,5]


def test_host_recovery_respects_intent_capacity_and_cooldown():
    spec=importlib.util.spec_from_file_location('guard',Path(__file__).parents[2]/'scripts/ops/projector_recovery.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    info={'Image':'current','State':{'Status':'exited','Error':'failed mount: no space left on device'}}
    kw=dict(enabled=True,paused=False,free=3*1024**3,last_attempt=0,now=1000)
    assert mod.eligible(info,'current',**kw)
    for key,value in [('enabled',False),('paused',True),('free',0),('last_attempt',900)]:
        assert not mod.eligible(info,'current',**{**kw,key:value})
    assert not mod.eligible(info,'other',**kw)
    info['State']['Status']='running'
    assert not mod.eligible(info,'current',**kw)


def test_commit_full_rolls_back_objects_and_checkpoint(tmp_path,monkeypatch):
    from doxagent.v2_read import repository
    store=ReadStore(tmp_path/'read.db');store.migrate()
    original=repository.sqlite3.connect
    class FailingCommit(sqlite3.Connection):
        def commit(self):raise full()
    def connect(*args,**kwargs):
        kwargs['factory']=FailingCommit
        return original(*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(repository.sqlite3,'connect',connect)
        with pytest.raises(sqlite3.OperationalError):
            store.ingest('runtime','1',[{'kind':'fixture','ticker':'','id':'x','data':{'ok':True}}],position=1)
    assert store.position('runtime')==0
    assert store.get('fixture','','x') is None
    store.ingest('runtime','1',[{'kind':'fixture','ticker':'','id':'x','data':{'ok':True}}],position=1)
    assert store.position('runtime')==1


def test_repair_old_parent_does_not_overwrite_newer_state(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    store.ingest('runtime','1',[],position=1,gap='ValueError')
    record=lambda value: [{'kind':'fixture','ticker':'','id':'x','data':{'value':value}}]
    store.ingest('runtime','5',record('new'),position=5)
    source=SimpleNamespace(source='runtime',event=lambda n: {'seq':n,'recorded_at':None})
    worker=ProjectionWorker(store,[source],lambda e:(record('old'),[]))
    assert worker.repair()==1
    assert store.position('runtime')==5
    assert store.get('fixture','','x')=={'value':'new'}
