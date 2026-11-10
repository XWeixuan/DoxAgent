"""A Worker replacement cannot race a durable new model submission."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest


def helper():
    path=Path(__file__).resolve().parents[1]/'scripts/ops/storage_lifecycle_release_20261111.py'
    spec=importlib.util.spec_from_file_location('storage_release_window',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('busy',['job','effect',None])
def test_worker_stop_is_idle_and_submission_fenced(tmp_path,busy):
    database=tmp_path/'jobs.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT)')
        if busy=='job':db.execute("INSERT INTO jobs VALUES('accepted','running')")
    stopped=[];guards=[]
    def guard(path):
        guards.append(path)
        if busy=='effect':raise ValueError('live result consumer')
    def stop(identity):
        with sqlite3.connect(database,timeout=0) as submitter:
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                submitter.execute("INSERT INTO jobs VALUES('new','queued')")
        stopped.append(identity)
    action=lambda:helper().stop_idle_worker(database,'runtime.sqlite3','immutable-worker-id',guard,stop=stop)
    if busy:
        with pytest.raises(ValueError):action()
    else:action()
    assert stopped==([] if busy else ['immutable-worker-id'])
    assert guards==([] if busy=='job' else ['runtime.sqlite3'])
    # Failure and success both release the short admission fence.
    with sqlite3.connect(database,timeout=0) as db:
        db.execute("INSERT INTO jobs VALUES('after','queued')")


def test_worker_only_does_not_require_scheduler_global_idleness(tmp_path):
    database=tmp_path/'jobs.db'
    with sqlite3.connect(database) as db:db.execute('CREATE TABLE jobs(status TEXT)')
    stopped=[]
    helper().stop_idle_worker(database,'serving-scheduler-runtime.db','worker',stop=stopped.append)
    assert stopped==['worker']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[500,503,'connect'])
async def test_completed_result_survives_worker_api_replacement_gap(monkeypatch,failure):
    import httpx
    from doxagent.codex_runtime.client import HttpCodexWorkerClient
    from tests.test_w3_provider_recovery import _job,_request
    request=_request('gpt-6-luna','trade').model_copy(update={'idempotency_key':'original','task_phase':'trade_decision'})
    completed=_job(request);calls=[]
    async def handle(http):
        calls.append((http.method,http.url.path))
        if len(calls)==1:
            if failure=='connect':raise httpx.ConnectError('Worker replacing',request=http)
            return httpx.Response(failure)
        return httpx.Response(200,json=completed.model_dump(mode='json'))
    client=HttpCodexWorkerClient('http://worker','token')
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://worker',transport=httpx.MockTransport(handle))
    async def no_wait(_):return None
    monkeypatch.setattr('doxagent.codex_runtime.client.asyncio.sleep',no_wait)
    try:
        result=await client.run(request,existing_job=completed.model_copy(update={'status':'running'}))
        assert result==completed
        assert calls==[('GET','/v1/jobs/'+completed.job_id)]*2
    finally:await client.aclose()
