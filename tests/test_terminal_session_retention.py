from contextlib import closing
from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
import pytest

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.schema import WorkerJob
from doxagent.codex_worker.session_retention import TerminalSessions


class SDK:
    def __init__(self,root,now):self.root,self.now,self.threads,self.deleted=root,now,{},[]
    def add(self,identity,parent=None,active=False):
        p=self.root/(identity+'.jsonl');p.write_text('session process output')
        self.threads[identity]=dict(id=identity,parentThreadId=parent,forkedFromId=None,sessionId=parent or identity,
             updatedAt=(self.now-timedelta(days=20)).timestamp(),path=str(p),status={'type':'active' if active else 'idle'})
    async def request(self,method,params):
        if method=='thread/list':
            assert 'subAgentThreadSpawn' in params['sourceKinds'] and params['useStateDbOnly']
            return {'data':[] if params['archived'] else list(self.threads.values()),'nextCursor':None}
        identity=params['threadId']
        if method=='thread/read':
            if identity not in self.threads:raise ValueError('thread not found')
            return {'thread':self.threads[identity]}
        assert method=='thread/delete'
        self.root.joinpath(identity+'.jsonl').unlink();self.threads.pop(identity);self.deleted.append(identity);return {}


def job(store,thread,now,*,status='succeeded',run=None,age=9):
    value=WorkerJob(job_id=thread,thread_id=thread,run_id=run or 'research-'+thread,attempt_id='1',status=status,
        final_response='{"final":"durable"}' if status=='succeeded' else None,error_code='ERROR' if status=='failed' else None,
        updated_at=now-timedelta(days=age),finished_at=now-timedelta(days=age))
    store.save(value);return value


@pytest.mark.asyncio
async def test_owned_terminal_tree_exits_but_final_result_and_w3_descendants_stay(tmp_path):
    now=datetime.now(UTC);store=JobStore(tmp_path);sdk=SDK(tmp_path,now)
    for t,parent in [('old',None),('child','old'),('grand','child'),('w3',None),('w3child','w3'),('w3grand','w3child'),('human',None)]:sdk.add(t,parent)
    job(store,'old',now);job(store,'w3',now,run='w3-template-protected',age=30)
    gc=TerminalSessions(SimpleNamespace(store=store),sdk)
    report=await gc.collect(now=now)
    assert set(report['deleted'])=={'old','child','grand'}
    assert {'w3','w3child','w3grand','human'}<=sdk.threads.keys()
    assert store.get('old').final_response=='{"final":"durable"}'
    with pytest.raises(ValueError,match='SESSION_RETIRED'):
        gc.validate_submit(SimpleNamespace(thread_id='old'))
    assert (await gc.collect(now=now))['deleted']==[]


@pytest.mark.asyncio
async def test_active_descendant_and_recovery_owner_protect_entire_tree(tmp_path):
    now=datetime.now(UTC);store=JobStore(tmp_path);sdk=SDK(tmp_path,now)
    for t,parent in [('parent',None),('active','parent'),('resume',None),('failed',None)]:sdk.add(t,parent)
    job(store,'parent',now);job(store,'active',now,status='running',age=1)
    job(store,'resume',now,run='held-workflow-o1');job(store,'failed',now,status='failed',age=10)
    gc=TerminalSessions(SimpleNamespace(store=store),sdk,owner_holds=lambda:{('run','held-workflow')})
    assert (await gc.collect(now=now))['deleted']==[]
    sdk.threads['active']['status']={'type':'active'}
    job(store,'active',now,age=20)
    assert (await gc.collect(now=now))['deleted']==[]
