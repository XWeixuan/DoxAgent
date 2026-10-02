import asyncio
import json
import sqlite3
import time

import pytest

from doxagent.api_v2.bus import aggregates_many
from doxagent.api_v2.errors import ApiFailure, classify_failure
from doxagent.api_v2.query_runner import QueryRunner, Slot
from doxagent.v2_read.content_files import ContentFiles, ContentUnavailable
from doxagent.v2_read.query_budget import deadline
from doxagent.v2_read.repository import ReadStore


@pytest.mark.parametrize('code,expected,retryable', [
    (sqlite3.SQLITE_BUSY, 'STORE_BUSY', True),
    (sqlite3.SQLITE_LOCKED, 'STORE_BUSY', True),
    (sqlite3.SQLITE_CANTOPEN, 'STORE_UNAVAILABLE', True),
    (sqlite3.SQLITE_CORRUPT, 'STORE_UNAVAILABLE', False),
    (sqlite3.SQLITE_INTERRUPT, 'STORE_UNAVAILABLE', False),
])
def test_safe_sqlite_categories(code, expected, retryable):
    exc = sqlite3.OperationalError('SECRET /private/path')
    exc.sqlite_errorcode = code
    failure = classify_failure(exc)
    assert (failure.code, failure.retryable) == (expected, retryable)
    assert 'SECRET' not in json.dumps(failure.payload('parent-request'))
    assert bool(failure.headers) == retryable


def test_deadline_and_missing_content(tmp_path):
    exc = sqlite3.OperationalError('interrupted')
    exc.sqlite_errorcode = sqlite3.SQLITE_INTERRUPT
    token = deadline.set(time.monotonic() - 1)
    try:
        assert classify_failure(exc).code == 'QUERY_TIMEOUT'
    finally:
        deadline.reset(token)
    assert classify_failure(exc, query_deadline=time.monotonic()-1).code == 'QUERY_TIMEOUT'
    assert classify_failure(exc, query_deadline=time.monotonic()+10).code == 'STORE_UNAVAILABLE'
    with pytest.raises(ContentUnavailable) as missing:
        ContentFiles(tmp_path).read('a' * 64, 0, 100)
    assert not classify_failure(missing.value).retryable
    assert classify_failure(ValueError('bad DTO SECRET')).code == 'INTERNAL_ERROR'


def test_scalar_reads_preserve_frozen_view_and_do_not_open_body(tmp_path, monkeypatch):
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    binding = {'kind':'native:ticker_source_bindings','ticker':'MU','id':'b','source_id':'s',
               'data':{'enabled':True,'polling':{'enabled':True}}}
    source = {'kind':'native:source_definitions','ticker':'','id':'s','data':{'enabled':True}}
    poll = {'kind':'native:poll_states','ticker':'MU','id':'b','data':{'status':'partial','last_latency_ms':1000}}
    message = {'kind':'message','ticker':'MU','id':'z','sort':'same',
               'data':{'stream_published_at':'old','body':'body ' * 10000}}
    seq = store.ingest('bus','one',[binding,source,poll,message,{**message,'id':'a','data':{'stream_published_at':'tie-loser'}}])
    later = store.ingest('bus','two',[{**source,'data':{'enabled':False}},
                                      {**poll,'data':{'status':'succeeded','last_latency_ms':2000}},
                                      {**message,'sort':'z-new','data':{'stream_published_at':'new'}}])
    monkeypatch.setattr(ContentFiles,'read',lambda *a: pytest.fail('scalar query opened body'))
    frozen = aggregates_many(store,['MU'],seq)['MU']
    assert frozen[0]['normal']['value'] == 0
    assert frozen[0]['abnormal']['value'] == 1
    assert frozen[1]['value'] == 1
    assert aggregates_many(store,['MU'],later)['MU'][0]['abnormal']['value'] == 0
    assert store.latest_message_at('MU',seq) == 'old'
    assert store.latest_message_at('MU',later) == 'new'
    assert store.latest_message_at('BE',seq) is None
    gap = store.ingest('bus','three',[{**binding,'id':'missing','source_id':'missing'}])
    assert aggregates_many(store,['MU'],gap)['MU'][0]['normal']['reason'] == 'SOURCE_GAP'
    tombstone = store.ingest('bus','four',[{**binding,'id':'missing','source_id':'missing','data':{'tombstoned_at':'now'}}])
    assert aggregates_many(store,['MU'],tombstone)['MU'][0]['normal']['value'] == 0


class Process:
    pid = 123
    alive = True
    def is_alive(self): return self.alive
    def terminate(self): self.alive = False
    def kill(self): self.alive = False
    def join(self, timeout): pass


class Pipe:
    sent = None
    closed = False
    def __init__(self, result=None): self.result = result
    def send(self, job): self.sent = job
    def poll(self): return self.sent is not None and self.result is not None
    def recv(self): return self.result
    def close(self): self.closed = True


@pytest.mark.asyncio
async def test_queue_budget_then_full_execution_and_soft_failure_reuses_pid(caplog):
    runner = QueryRunner(queue_seconds=.2)
    pipe = Pipe((True,(504,{},b'{}',{'code':'QUERY_TIMEOUT'})))
    slot = Slot(Process(),pipe,1,'READY')
    runner.slots.append(slot)
    async def release():
        await asyncio.sleep(.04)
        runner.available.put_nowait(slot)
    task = asyncio.create_task(release())
    started = time.monotonic()
    try:
        response = await runner.run({'kind':'http','request_id':'parent-id','route':'/safe/{ticker}'},timeout=.1)
        assert response[0] == 504
        assert pipe.sent['deadline'] >= started + .13
        assert slot.state == 'READY' and slot.process.is_alive()
        assert 'parent-id' in caplog.text and 'broken=False' in caplog.text
    finally:
        await task
        await runner.close()


@pytest.mark.asyncio
async def test_capacity_hard_timeout_and_cancellation():
    runner = QueryRunner(queue_seconds=.02,cancel_grace=.01)
    try:
        with pytest.raises(ApiFailure) as busy:
            await runner.run({'kind':'http'},timeout=.01)
        assert busy.value.code == 'SERVICE_BUSY'
        pipe = Pipe()
        slot = Slot(Process(),pipe,1,'READY')
        runner.slots.append(slot)
        runner.available.put_nowait(slot)
        with pytest.raises(ApiFailure) as timeout:
            await runner.run({'kind':'http'},timeout=.01)
        assert timeout.value.code == 'QUERY_TIMEOUT'
        assert pipe.closed and not slot.process.is_alive()
        fresh = Slot(Process(),Pipe(),2,'READY')
        runner.slots.append(fresh)
        runner.available.put_nowait(fresh)
        task = asyncio.create_task(runner.run({'kind':'http'},timeout=1))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert fresh.pipe.closed
        assert runner.available.qsize() == 0
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_replacements_handshake_in_parallel_but_bounded(monkeypatch):
    runner = QueryRunner(workers=2)
    started = []
    release = asyncio.Event()
    async def new():
        slot = Slot(Process(),Pipe(),len(started)+1)
        runner.slots.append(slot)
        started.append(slot)
        await release.wait()
        slot.state = 'READY'
        return slot
    monkeypatch.setattr(runner,'_new',new)
    runner.supervisor = asyncio.create_task(runner._supervise())
    try:
        await asyncio.sleep(.03)
        assert len(started) == 2 and len(runner.slots) == 2
        await asyncio.sleep(.12)
        assert len(started) == 2
        release.set()
        await asyncio.sleep(.03)
        assert runner.available.qsize() == 2
    finally:
        release.set()
        await runner.close()



@pytest.mark.parametrize('kind,status,code,retryable', [
    ('busy',503,'STORE_BUSY',True),
    ('deadline',504,'QUERY_TIMEOUT',True),
    ('content',503,'CONTENT_UNAVAILABLE',False),
    ('dto',500,'INTERNAL_ERROR',False),
])
def test_http_failure_envelope_has_safe_correlated_diagnostics(tmp_path, caplog, kind, status, code, retryable):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import create_app, PREFIX
    from doxagent.api_v2.auth import Principal
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_control.repository import ControlRepository
    from doxagent.v2_read.query_budget import QueryDeadlineExceeded
    class Auth:
        async def authenticate(self, token): return Principal('owner','DEVELOPER',time.time()+60)
    store = ReadStore(tmp_path/'read.db')
    store.migrate()
    control = ControlRepository(RuntimeJournal(tmp_path/'runtime.db'))
    control.migrate()
    app = create_app(store=store,control=control,auth=Auth())
    @app.get(PREFIX + '/fault/{identity}')
    async def injected(identity: str):
        if kind == 'busy':
            exc = sqlite3.OperationalError('SECRET business content')
            exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
            exc.sqlite_errorname = 'SQLITE_BUSY'
            raise exc
        if kind == 'deadline': raise QueryDeadlineExceeded()
        if kind == 'content': raise ContentUnavailable('SECRET /path')
        raise ValueError('SECRET DTO')
    with TestClient(app) as client:
        result = client.get(PREFIX+'/fault/SECRET',headers={'Authorization':'Bearer offline'})
    error = result.json()['error']
    assert (result.status_code,error['code'],error['retryable']) == (status,code,retryable)
    assert bool(result.headers.get('Retry-After')) == retryable
    assert error['request_id'] in caplog.text
    assert '/fault/{identity}' in caplog.text
    assert 'SECRET' not in caplog.text + result.text


def test_overview_filters_and_keyset_paging_keep_the_saved_view(tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI, Request
    from doxagent.api_v2.overview import install
    store=ReadStore(tmp_path/'read.db')
    store.migrate()
    def row(ticker, run='RUNNING', health='NORMAL'):
        return {'kind':'ticker','ticker':ticker,'id':ticker,'data':{
            'ticker':ticker,'run_state':run,'health':health,'initialization_id':None,'initialization_incomplete':False}}
    seq=store.ingest('fixture','one',[row('A'),row('B','PAUSED'),row('C')])
    # A changes and D appears after opening this view. Paging must retain A/C.
    store.ingest('fixture','two',[row('A','STOPPED','BLOCKED'),row('D')])
    view={'seq':seq,'tickers':['A','B','C'],'wire':{'page':'OVERVIEW','period':{
        'current':{'trading_days':['2026-10-02']},'previous':None}}}
    frozen_id=store.save_token('owner','test',view,view=True)
    app=FastAPI()
    app.state.store=store
    app.state.control=None
    app.state.views=SimpleNamespace(get=lambda *args: view)
    app.state.query=lambda request, allowed: dict(request.query_params)
    app.state.respond=lambda request, name, payload, **kwargs: payload
    install(app)
    endpoint=next(r.endpoint for r in app.routes if r.path.endswith('/overview/tickers'))
    def fetch(params):
        request=Request({'type':'http','path':'/overview/tickers','headers':[],
                         'query_string':params.replace('frozen',frozen_id).encode(),'state':{'principal':SimpleNamespace(user_id='owner')}})
        return asyncio.run(endpoint(request))
    first=fetch('view_id=frozen&limit=1&run_state=RUNNING&health=NORMAL')
    assert [r['state']['run_state'] for r in first['items']]==['RUNNING']
    assert first['items'][0]['state']['ticker']=='A'
    assert first['has_more'] and first['next_cursor']
    second=fetch('view_id=frozen&limit=1&run_state=RUNNING&health=NORMAL&cursor='+first['next_cursor'])
    assert second['items'][0]['state']['ticker']=='C'
    assert len(second['items'])==1 and not second['has_more']
    assert first['items'][0]['state'] != row('A','STOPPED','BLOCKED')['data']
    assert fetch('view_id=frozen&limit=1&health=BLOCKED')['items']==[]
    assert len(fetch('view_id=frozen&run_state=PAUSED')['items'])==1
