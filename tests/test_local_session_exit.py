from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.local_sessions import prepare,consumed,collect
from tests.test_terminal_session_retention import SDK


@pytest.mark.asyncio
async def test_local_root_waits_for_exact_owner_acks_without_sdk_listing(tmp_path,monkeypatch):
    import json
    monkeypatch.setenv('CODEX_HOME',str(tmp_path))
    native=tmp_path/'native';native.mkdir();(native/'manifest.json').write_text(json.dumps({'storage_local_root_exit':True}))
    now=datetime.now(UTC);store=JobStore(tmp_path);sdk=SDK(tmp_path,now);sdk.add('root')
    run='runtime-maintain-'+'a'*24+'-o2'
    with store.connect() as db,db:
        for identity,thread in [('first',None),('next','root')]:
            job=SimpleNamespace(job_id=identity,run_id=run,thread_id='root',status='succeeded')
            request=SimpleNamespace(thread_id=thread,max_subagents=0)
            prepare(db,job,request)
            db.execute("INSERT INTO jobs(id,status,payload) VALUES(?,'succeeded','{}')",(identity,))
        consumed(db,'first',now.isoformat())
    jobs=SimpleNamespace(store=store,thread_lifecycle=SimpleNamespace(transport=sdk),_runtime=SimpleNamespace(),_retention_settings=SimpleNamespace(codex_runtime_bin=str(native/'codex')))
    assert (await collect(jobs,now=now))['deferred']==['root']
    assert sdk.deleted==[]
    with store.connect() as db,db:
        consumed(db,'next',now.isoformat())
    result=await collect(jobs,now=now+timedelta(minutes=16))
    assert result['deleted']==['root']
    assert not (tmp_path/'root.jsonl').exists()
    assert not (await collect(jobs,now=now+timedelta(minutes=17)))['deleted']
