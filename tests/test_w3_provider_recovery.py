"""Provider admission never owns successful results or unknown dispatches."""
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from threading import Event, Thread
import time

import httpx
import pytest

from doxagent.codex_runtime.client import HttpCodexWorkerClient, dispatch_job_id
from doxagent.codex_runtime.errors import W3PreparationError
from doxagent.codex_worker.schema import WorkerJob
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.worker_receipts import ReceiptWorker
from tests.test_persistent_runtime_worker_receipts import _request


class Worker:
    def __init__(self, clock):
        self.calls = []
        self.clock = clock
        self.error = None

    async def run(self, request):
        self.calls.append(request)
        return WorkerJob(job_id=f'job-{len(self.calls)}', run_id=request.run_id,
                         attempt_id=request.attempt_id,
                         status='failed' if self.error else 'succeeded',
                         error_message=self.error, finished_at=self.clock())


def fixture(tmp_path):
    now = [datetime.now(UTC)]
    journal = RuntimeJournal(tmp_path/'runtime.db', clock=lambda: now[0])
    worker = Worker(lambda: now[0])
    request = _request('gpt-6-luna','trade').model_copy(update={'task_phase':'trade_decision'})
    return now, journal, worker, request


@pytest.mark.asyncio
@pytest.mark.parametrize('expired', [False, True])
async def test_success_during_another_pause_is_consumed_without_owning_probe(tmp_path, expired):
    now, journal, worker, request = fixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    run = worker.run
    async def waiting(request):
        entered.set()
        await release.wait()
        return await run(request)
    worker.run = waiting
    receipt = ReceiptWorker(worker,journal,'B',case_id='B')
    task = asyncio.create_task(receipt.run(request))
    await asyncio.wait_for(entered.wait(),2)
    pause = dict(job_id='A-failure',code='usage_limit_exceeded',
                 until=(now[0]+timedelta(seconds=-1 if expired else 900)).isoformat())
    journal.set('w3_provider_pause','codex',pause)
    release.set()
    job = await asyncio.wait_for(task,2)
    assert job.status == 'succeeded'
    assert journal.get('w3_provider_pause','codex') == pause
    assert (await receipt.run(request)).job_id == job.job_id
    assert len(worker.calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_is_local_and_original_deadline_does_not_move(tmp_path):
    now,journal,worker,request = fixture(tmp_path)
    worker.error = 'http_status_code=429'
    receipt = ReceiptWorker(worker,journal,'A',case_id='A')
    with pytest.raises(W3PreparationError) as first:
        await receipt.run(request)
    assert first.value.provider_wait['reason'] == 'rate_limit'
    assert journal.get('w3_provider_pause','codex') is None
    deadline = first.value.retry_at
    now[0] += timedelta(seconds=20)
    with pytest.raises(W3PreparationError) as second:
        await receipt.run(request)
    assert second.value.retry_at == deadline
    assert len(worker.calls) == 1
    worker.error = None
    assert (await ReceiptWorker(worker,journal,'B',case_id='B').run(request)).status == 'succeeded'
    now[0] += timedelta(seconds=41)
    assert (await receipt.run(request)).status == 'succeeded'
    assert len(worker.calls) == 3


@pytest.mark.asyncio
async def test_probe_ownership_is_fixed_and_late_success_cannot_clear_new_failure(tmp_path):
    now,journal,worker,request = fixture(tmp_path)
    receipt = ReceiptWorker(worker,journal,'A',case_id='A')
    receipt.last_identity = 'A'
    request = request.model_copy(update={'idempotency_key':'dispatch-A'})
    journal.set('w3_provider_pause','codex',dict(job_id='failure-A',code='usage_limit_exceeded',
                until=(now[0]-timedelta(seconds=1)).isoformat()))
    token = await receipt._admit(request,None)
    original = journal.get('w3_provider_pause','codex')
    now[0] += timedelta(seconds=30)
    assert await receipt._admit(request,None) == token
    assert journal.get('w3_provider_pause','codex') == original
    with pytest.raises(W3PreparationError):
        await receipt._admit(request.model_copy(update={'idempotency_key':'dispatch-B'}),None)
    newer = dict(job_id='failure-new',code='usage_limit_exceeded',
                 until=(now[0]+timedelta(seconds=900)).isoformat())
    journal.set('w3_provider_pause','codex',newer)
    job = await worker.run(request)
    with journal.transaction() as db:
        receipt._provider_outcome(db,job,token,{'provider_outcome':None},fresh=True)
    assert journal.get('w3_provider_pause','codex') == newer


def _job(request):
    payload=request.model_dump(mode='json')
    for name in ('task_phase','web_search_mode'):
        if payload.get(name) is None:payload.pop(name,None)
    return WorkerJob(job_id=dispatch_job_id(request),run_id=request.run_id,
                     attempt_id=request.attempt_id,status='succeeded',final_response='{}',
                     request_sha256=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest())


@pytest.mark.asyncio
async def test_post_timeout_reconciles_original_job_without_second_post(monkeypatch):
    request=_request('gpt-6-luna','trade').model_copy(update={'task_phase':'trade_decision',
                                                          'idempotency_key':'original'})
    job=_job(request)
    calls=[]
    accepted=False
    async def handle(http):
        nonlocal accepted
        calls.append(http.method)
        if http.method=='GET':
            return httpx.Response(200,json=job.model_dump(mode='json')) if accepted else httpx.Response(404)
        accepted=True
        raise httpx.ReadTimeout('response lost after acceptance')
    client=HttpCodexWorkerClient('http://worker','token')
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://worker',transport=httpx.MockTransport(handle))
    async def yield_once(_):return None
    monkeypatch.setattr('doxagent.codex_runtime.client.asyncio.sleep',yield_once)
    try:
        assert (await client.run(request)).job_id==job.job_id
        assert calls==['GET','POST','GET']
    finally:await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[500,503,'timeout'])
async def test_unknown_lookup_never_dispatches_or_changes_generation(tmp_path,failure):
    _,journal,_,request=fixture(tmp_path)
    calls=[]
    async def handle(http):
        calls.append(http.method)
        if failure=='timeout':raise httpx.ReadTimeout('unknown')
        return httpx.Response(failure)
    client=HttpCodexWorkerClient('http://worker','token')
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://worker',transport=httpx.MockTransport(handle))
    receipt=ReceiptWorker(client,journal,'A',case_id='A')
    try:
        with pytest.raises(W3PreparationError,match='HTTPStatusError|ReadTimeout') as error:
            await receipt.run(request)
        assert error.value.code=='w3_dispatch_unknown'
        assert calls==['GET']
        frozen=journal.get('worker_requests',receipt.last_identity)
        with pytest.raises(W3PreparationError):await receipt.run(request)
        assert journal.get('worker_requests',receipt.last_identity)==frozen
        assert journal.get('worker_generation',receipt.last_identity,0)==0
        assert 'POST' not in calls
    finally:await client.aclose()


@pytest.mark.asyncio
async def test_completed_result_short_writer_handles_300ms_contention_without_timeout_change(tmp_path):
    from doxagent.control_io import write_io
    from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry
    import sqlite3
    journal=RuntimeJournal(tmp_path/'runtime.db')
    registry=W3ThreadRegistry(journal)
    registry.begin_stage('case','trade_decision')
    entered=Event()
    def hold():
        with sqlite3.connect(journal.path) as db:
            db.execute('BEGIN IMMEDIATE');entered.set();time.sleep(.3)
    thread=Thread(target=hold);thread.start();assert entered.wait(1)
    start=time.perf_counter()
    await write_io(journal.path,registry.save_result,'case','trade_decision',{},
                   thread_id='thread',priority=0,retry=True)
    elapsed=time.perf_counter()-start;thread.join()
    assert .15<elapsed<1
    assert registry.stage('case','trade_decision')['status']=='RESULT_SAVED'
    assert journal.busy_timeout_seconds==30  # unchanged configuration


def test_pause_removed_rebases_all_batches_but_not_other_retries(tmp_path):
    from tests.test_w3_v4 import runtime,enqueue
    from doxagent.persistent_runtime_v2.schema import RuntimeEffect
    service,journal,_,_=runtime(tmp_path)
    case=enqueue(service)
    future=datetime.now(UTC)+timedelta(minutes=10)
    try:
        for n in range(150):
            service.repository.enqueue_effect(RuntimeEffect(case_id=case.case_id,
                effect_type='route_to_w3',idempotency_key=f'wait-{n}',status='PENDING_RETRY',
                available_at=future,payload={'provider_wait':{'reason':'usage_limit','pause_job_id':'old'}},
                last_error='Codex new request waiting: usage_limit'))
        other=service.repository.enqueue_effect(RuntimeEffect(case_id=case.case_id,
            effect_type='route_to_w3',idempotency_key='unrelated',status='PENDING_RETRY',
            available_at=future,last_error='business transient failure'))
        assert service.repository.reconcile_provider_waits(limit=100)==100
        assert service.repository.reconcile_provider_waits(limit=100)==50
        assert service.repository.reconcile_provider_waits(limit=100)==0
        saved=next(e for e in service.repository.list_effects(case.case_id) if e.effect_id==other.effect_id)
        assert saved.available_at==future
        assert saved.attempt_count==0
    finally:service.close()


def test_failed_future_consumes_original_error_without_preparing_again(tmp_path,monkeypatch):
    from concurrent.futures import Future
    from tests.test_w3_v4 import runtime,enqueue
    from doxagent.persistent_runtime_v2.w3 import W3Error
    service,_,agent,_=runtime(tmp_path)
    case=enqueue(service)
    prepared=[];pending=Future()
    original=agent.prepare_case
    original_run=agent.run_phase
    def prepare(case):prepared.append(case.case_id);return original(case)
    def run(**kwargs):
        if kwargs['phase']!='trade_decision':return original_run(**kwargs)
        error=W3Error('w3_pending_external','in flight')
        error.pending_future=pending;error.phase_result=pending.result
        raise error
    monkeypatch.setattr(agent,'prepare_case',prepare);monkeypatch.setattr(agent,'run_phase',run)
    try:
        assert service.dispatch_pending_effects()==1
        until=time.monotonic()+2
        while not service._phase_waiters and time.monotonic()<until:time.sleep(.005)
        assert service._phase_waiters
        pending.set_exception(W3PreparationError('w3_dispatch_unknown','unavailable original job'))
        until=time.monotonic()+2
        while service._active_effect_ids and time.monotonic()<until:time.sleep(.005)
        assert prepared==[case.case_id]
        assert not service._effect_continuations and not service._phase_waiters
        effect=service.repository.list_effects(case.case_id)[0]
        assert effect.status=='PENDING_RETRY' and effect.attempt_count==0
    finally:
        if not pending.done():pending.cancel()
        service.close()


@pytest.mark.asyncio
async def test_accepted_job_recovers_even_during_quota_pause(tmp_path):
    now,journal,worker,request=fixture(tmp_path)
    receipt=ReceiptWorker(worker,journal,'A',case_id='A')
    key=f'runtime-worker:A:{request.run_id}:persistent_runtime_w3:trade_decision:A'
    from doxagent.persistent_runtime_v2.journal import digest
    frozen=request.model_copy(update={'idempotency_key':digest(key)})
    journal.set('worker_requests',key,frozen.model_dump(mode='json'))
    journal.set('w3_provider_pause','codex',dict(job_id='quota',code='usage_limit_exceeded',
                until=(now[0]+timedelta(minutes=15)).isoformat()))
    job=_job(frozen)
    async def lookup(req):assert req.idempotency_key==frozen.idempotency_key;return job
    async def resume(req,existing,**kwargs):assert existing.job_id==job.job_id;return existing
    worker.lookup_job=lookup;worker.resume_job=resume
    assert (await receipt.run(request)).job_id==job.job_id
    assert not worker.calls
    assert journal.get('worker_generation',key,0)==0


@pytest.mark.asyncio
async def test_legacy_success_assigned_a_phantom_probe_is_not_treated_as_new_recovery(tmp_path):
    now,journal,worker,request=fixture(tmp_path)
    legacy='runtime-worker:old:run:persistent_runtime_w3:trade_decision:old'
    frozen=request.model_copy(update={'idempotency_key':'old-dispatch'})
    journal.set('worker_requests',legacy,frozen.model_dump(mode='json'))
    old=_job(frozen).model_copy(update={'finished_at':now[0]-timedelta(hours=1)})
    async def lookup(req):assert req.idempotency_key=='old-dispatch';return old
    worker.lookup_job=lookup
    journal.set('w3_provider_pause','codex',dict(job_id='quota',code='usage_limit_exceeded',
        until=(now[0]-timedelta(seconds=1)).isoformat(),probe_owner=legacy,
        probe_until=(now[0]+timedelta(minutes=9)).isoformat()))
    receipt=ReceiptWorker(worker,journal,'new');receipt.last_identity='new'
    new=request.model_copy(update={'idempotency_key':'new-dispatch'})
    token=await receipt._admit(new,None)
    assert token==('quota',dispatch_job_id(new))
    assert journal.get('w3_provider_pause','codex')['job_id']=='quota'


@pytest.mark.asyncio
async def test_new_ready_claim_does_not_wait_for_background_inventory_mutex(tmp_path):
    from contextlib import closing
    from doxagent.codex_worker.job_store import JobStore
    from doxagent.codex_worker.thread_lifecycle import WorkerThreadLifecycle
    from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
    from doxagent.codex_worker.w3_ready import W3ReadySlots
    from tests.test_w3_ready_v5 import Runtime
    workspaces=LocalWorkspaceStore(tmp_path)
    jobs=SimpleNamespace(store=JobStore(tmp_path),_runtime=Runtime(),_startup=asyncio.Semaphore(3))
    jobs.thread_lifecycle=WorkerThreadLifecycle(jobs,workspaces)
    with closing(jobs.store.connect()) as db,db:
        db.execute("INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')")
    slots=W3ReadySlots(jobs,workspaces,SimpleNamespace(w3_ready_luna=0,w3_ready_sol=0),SimpleNamespace(ready={}))
    profile=dict(model='gpt-6-luna',provider=None,effort='max')
    await slots.configure(dict(parent_thread_id='parent',profile=profile,instructions={'AGENTS.md':'public'}))
    try:
        async with slots.lock:
            result=await asyncio.wait_for(slots.claim(dict(case_id='case',record_id='record',
                parent_thread_id='parent',profile=profile,execution_lane='realtime'),wait=True),2)
            assert result['status']=='COMPLETED'
        assert (await slots.claim(dict(case_id='case',record_id='record',parent_thread_id='parent',
                                     profile=profile)))['slot_id']==result['slot_id']
    finally:
        await slots.close();await jobs.thread_lifecycle.close()


def test_new_effect_owner_does_not_inherit_finished_old_lease_error():
    from concurrent.futures import Future
    from threading import Lock
    from doxagent.persistent_runtime_v2.fencing import write_scope
    from doxagent.persistent_runtime_v2.journal import LeaseLost
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
    runner=object.__new__(CodexW3AgentRunner)
    runner._closed=False;runner._pending_v4_lock=Lock();runner._timeout_seconds=1
    runner.environment=SimpleNamespace(mode='PRODUCTION')
    old=Future();old.effect_owner=('effect',1);old.set_exception(LeaseLost('old owner'))
    runner._pending_v4={'phase':old};runner._loop=asyncio.new_event_loop()
    thread=Thread(target=runner._loop.run_forever);thread.start()
    calls=[]
    async def recover():calls.append('original Job lookup');return 'recovered'
    try:
        with write_scope('effect',SimpleNamespace(effect_id='effect',lease_token=2),lambda:datetime.now(UTC)):
            assert runner._wait_v4(recover(),key='phase')=='recovered'
        assert calls==['original Job lookup'] and not runner._pending_v4
    finally:
        runner._loop.call_soon_threadsafe(runner._loop.stop);thread.join();runner._loop.close()


@pytest.mark.asyncio
async def test_terminal_transaction_rolls_back_both_receipts_and_recovers_original_job(tmp_path, monkeypatch):
    _,journal,worker,request=fixture(tmp_path)
    receipt=ReceiptWorker(worker,journal,'A',case_id='A')
    outcome=receipt._provider_outcome
    def crash(*args,**kwargs):raise RuntimeError('before transaction commit')
    monkeypatch.setattr(receipt,'_provider_outcome',crash)
    with pytest.raises(RuntimeError,match='before transaction commit'):
        await receipt.run(request)
    assert journal.get('worker_receipts',receipt.last_identity) is None
    assert journal.get('worker_invocations','job-1') is None
    original=worker.calls[0]
    job=WorkerJob(job_id='job-1',run_id=original.run_id,attempt_id=original.attempt_id,status='succeeded')
    async def lookup(req):assert req.idempotency_key==original.idempotency_key;return job
    async def resume(req,existing,**kwargs):return existing
    worker.lookup_job=lookup;worker.resume_job=resume
    monkeypatch.setattr(receipt,'_provider_outcome',outcome)
    assert (await receipt.run(request)).job_id=='job-1'
    assert len(worker.calls)==1
    assert journal.get('worker_receipts',receipt.last_identity)['job_id']=='job-1'
    assert journal.get('worker_invocations','job-1')['job']['job_id']=='job-1'


@pytest.mark.asyncio
async def test_probe_cancelled_before_post_releases_ownership_not_frozen_request(tmp_path):
    now,journal,_,request=fixture(tmp_path)
    entered=asyncio.Event();calls=[]
    async def handle(http):
        calls.append(http.method)
        assert http.method=='GET'
        if len(calls)==1:return httpx.Response(404)
        entered.set()
        await asyncio.Event().wait()
    client=HttpCodexWorkerClient('http://worker','token')
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://worker',transport=httpx.MockTransport(handle))
    journal.set('w3_provider_pause','codex',dict(job_id='quota',code='usage_limit_exceeded',
                until=(now[0]-timedelta(seconds=1)).isoformat()))
    receipt=ReceiptWorker(client,journal,'A',case_id='A')
    task=asyncio.create_task(receipt.run(request))
    try:
        await asyncio.wait_for(entered.wait(),2)
        frozen=journal.get('worker_requests',receipt.last_identity)
        assert journal.get('w3_provider_pause','codex')['probe_owner']==dispatch_job_id(
            request.model_copy(update={'idempotency_key':frozen['idempotency_key']}))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert 'probe_owner' not in journal.get('w3_provider_pause','codex')
        assert journal.get('worker_requests',receipt.last_identity)==frozen
        assert calls==['GET','GET']
    finally:
        if not task.done():task.cancel()
        await client.aclose()


@pytest.mark.asyncio
async def test_probe_non_quota_failure_recovers_provider_but_keeps_case_failure(tmp_path):
    now,journal,worker,request=fixture(tmp_path)
    worker.error='invalid model output'
    wakes=[]
    journal.set('w3_provider_pause','codex',dict(job_id='quota',code='usage_limit_exceeded',
                until=(now[0]-timedelta(seconds=1)).isoformat()))
    receipt=ReceiptWorker(worker,journal,'A',case_id='A',on_recovery=lambda:wakes.append(True))
    job=await receipt.run(request)
    assert job.status=='failed' and job.error_message=='invalid model output'
    assert journal.get('w3_provider_pause','codex') is None
    assert wakes==[True]
