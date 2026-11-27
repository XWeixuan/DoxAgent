import asyncio,json,sqlite3
from contextlib import closing
from datetime import datetime,UTC,timedelta
from types import SimpleNamespace
import pytest
from doxagent.persistent_runtime_v2.fencing import write_scope,assert_write_lease
from doxagent.persistent_runtime_v2.schema import RuntimeEffect
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner,W3Error
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry

def test_dual_completed_results_saved_by_new_owner(tmp_path):
 p=tmp_path/'r.db'
 with closing(sqlite3.connect(p)) as db,db:
  db.execute('create table runtime_v2_effects(effect_id text,status text,payload_json text)')
  db.execute('insert into runtime_v2_effects values(?,?,?)',('e','RUNNING',json.dumps(dict(lease_token=1,lease_until=(datetime.now(UTC)+timedelta(minutes=2)).isoformat()))))
 runner=object.__new__(CodexW3AgentRunner);runner._journal=SimpleNamespace(path=p)
 def wait(coro,**kw):
  value=asyncio.run(coro)
  if kw.get('key'):
   exc=W3Error('w3_pending_external','original Future');exc.phase_result=lambda:value;raise exc
  return value
 runner._wait_v4=wait
 async def phase(**kw):
  kw['shared_trade']['ready'].set()
  with closing(sqlite3.connect(p)) as db,db:db.execute("update runtime_v2_effects set payload_json=json_set(payload_json,'$.lease_token',2)")
  return {},SimpleNamespace()
 runner._run_phase=phase;saved=[]
 def save(label,*unused):
  from doxagent.control_io import SHORT_WRITE
  assert not SHORT_WRITE.get(), 'result preparation cannot occupy the short SQL owner'
  with closing(sqlite3.connect(p)) as db:assert_write_lease(db)
  saved.append(label)
 e=RuntimeEffect(effect_id='e',case_id='c',effect_type='route_to_w3',idempotency_key='c',lease_token=1)
 with write_scope('effect',e,lambda:datetime.now(UTC)),pytest.raises(W3Error) as error:
  runner.run_trade_pair(case=SimpleNamespace(case_id='c'),records={'A':{},'B':{}},payload={},context=None,research=None,save=save)
 assert saved==[]
 with write_scope('effect',e.model_copy(update={'lease_token':2}),lambda:datetime.now(UTC)):
  error.value.phase_result()
 assert saved==['A','B']

def test_template_query_uses_existing_partial_index(tmp_path):
 import ast,inspect,textwrap
 j=RuntimeJournal(tmp_path/'j.db');W3ThreadRegistry(j)
 tree=ast.parse(textwrap.dedent(inspect.getsource(W3ThreadRegistry.template)))
 queries=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith('SELECT') and 'template_key' in n.value]
 assert len(queries)==2
 with j.transaction(write=False) as db:
  for query in queries:
   plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+query,('key',)))
   assert 'USING INDEX w3_template_key' in plan

def test_dirty_notifications_do_not_scan_or_spawn_supplements(tmp_path,monkeypatch):
 from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
 j=RuntimeJournal(tmp_path/'j.db')
 with j.transaction() as db:
  db.execute('create table runtime_v2_cases(case_id text primary key,ticker text,trading_date text,payload_json text)')
  for i in range(40):
   cid=f'c{i:03}';db.execute('insert into runtime_v2_cases values(?,?,?,?)',(cid,'MU','2020-01-01','{}'))
   db.execute('insert into runtime_values values(?,?,?)',('dirty_case',cid,json.dumps(dict(case_id=cid,ticker='MU'))))
 c=RuntimeCoordinator(SimpleNamespace(repository=SimpleNamespace(path=j.path)),j);seen=[]
 monkeypatch.setattr(c,'_schedule_case_supplement',lambda dirty,parents:seen.append(dirty['case_id']))
 try:
  c._schedule_supplements('MU');assert seen==[]
  c._schedule_supplements('MU');assert seen==[]
 finally:c.close()

@pytest.mark.asyncio
async def test_ack_release_is_terminal_only_and_preserves_thread_history(tmp_path):
 import httpx
 from unittest.mock import AsyncMock
 from doxagent.codex_worker.app import create_worker_app
 from doxagent.codex_worker.schema import WorkerJob
 runtime=SimpleNamespace(release_thread=AsyncMock())
 app=create_worker_app(workspace_root=str(tmp_path),bearer_token='s'*32,capability_secret='k'*32,runtime=runtime)
 jobs=app.state.jobs
 jobs.store.save(WorkerJob(job_id='j',run_id='r',attempt_id='a',status='running',thread_id='t'))
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+'s'*32}) as client:
  assert (await client.post('/v1/jobs/j/acknowledge?release_resident=true')).status_code==409
  runtime.release_thread.assert_not_awaited()
  jobs.store.save(jobs.store.get('j').model_copy(update={'status':'succeeded'}))
  assert (await client.post('/v1/jobs/j/acknowledge?release_resident=true')).status_code==200
  runtime.release_thread.assert_awaited_once_with('t')
  assert jobs.store.get('j').thread_id=='t' and jobs.store.get('j').status=='succeeded'


def test_market_cleanup_cannot_block_committed_trade(tmp_path):
 from threading import Event
 from concurrent.futures import ThreadPoolExecutor
 from tests.test_w3_v5_execution import setup_case
 service,journal,agent,case,_=setup_case(tmp_path)
 entered,release=Event(),Event();original=agent.run_phase
 def run(**kwargs):
  value=original(**kwargs)
  if kwargs['phase']=='trade_decision':
   agent.entry_enabled=lambda _:True
  return value
 def stop(_):
  entered.set();release.wait(10)
 agent.run_phase=run;agent.stop_market=stop
 try:
  with ThreadPoolExecutor(max_workers=1) as executor:
   future=executor.submit(service.process_pending_effects)
   try:
    assert entered.wait(5)
    assert future.result(timeout=2)==1
    assert service._v4_execution().registry.stage(case.case_id,'trade_decision')['status']=='COMMITTED'
    assert not release.is_set()
   finally:release.set()
 finally:service.close()


def test_new_cases_dispatch_while_recovery_tick_is_busy_without_duplicate_or_cap_change(tmp_path):
 from threading import Event
 from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
 j=RuntimeJournal(tmp_path/'j.db');c=RuntimeCoordinator(SimpleNamespace(),j)
 entered,release=Event(),Event();calls=[]
 def run(task):
  calls.append(task['id'])
  if len(calls)==j.CASE_CONCURRENCY_PER_TICKER:entered.set()
  release.wait(10)
 c._execute=run
 for i in range(j.CASE_CONCURRENCY_PER_TICKER+1):
  j.put_task(f'new-{i}','MU','CASE',dict(mode='REALTIME'))
 j.put_task('closed','MU','CASE',dict(mode='CLOSED'))
 try:
  with c._dispatch_lock:  # An unrelated recovery/calendar pass is still busy.
   c.dispatch_realtime('MU')
   assert entered.wait(5)
   c.dispatch_realtime('MU')
   assert len(calls)==len(set(calls))==j.CASE_CONCURRENCY_PER_TICKER
   assert j.get_task('closed')['status']=='PENDING'
   assert sum(j.get_task(f'new-{i}')['status']=='PENDING'
              for i in range(j.CASE_CONCURRENCY_PER_TICKER+1))==1
 finally:release.set();c.close()


def test_branch_result_and_audit_share_one_rollback_and_encoding_is_outside_writer(tmp_path,monkeypatch):
 from tests.test_w3_v5_execution import setup_case
 from tests.test_w3_v5_contracts import direction
 from doxagent.control_io import SHORT_WRITE
 from doxagent.persistent_runtime_v2 import w3_execution
 s,j,agent,case,_=setup_case(tmp_path)
 case.frozen_inputs['w3_trade_mode']='dual_consensus';s.repository.save_case(case)
 execution=s._v4_execution();original=w3_execution.encode;prepared=[]
 def encode(value):
  if isinstance(value,dict) and 'input_sha' in value:
   assert not SHORT_WRITE.get()
   prepared.append(True)
  return original(value)
 monkeypatch.setattr(w3_execution,'encode',encode)
 def pair(**kw):
  record=kw['records']['A']
  job=SimpleNamespace(job_id='rollback-A',thread_id=record['thread_id'],started_at=None,
                      finished_at=None,final_response=json.dumps(direction('NO_TRADE')))
  kw['save']('A',direction('NO_TRADE'),job)
 agent.run_trade_pair=pair
 try:
  with j.transaction() as db:
   db.execute("CREATE TRIGGER abort_branch_audit BEFORE UPDATE OF input_refs ON runtime_v2_w3_stages "
              "WHEN NEW.phase='trade_decision:A' AND NEW.status='RESULT_SAVED' "
              "BEGIN SELECT RAISE(ABORT,'audit rollback'); END")
  assert s.process_pending_effects()==0
  stage=execution.registry.stage(case.case_id,'trade_decision:A')
  assert prepared and stage['result_json'] is None and stage['status']=='PENDING'
  assert j.get('w3_ack_pending','rollback-A') is None
 finally:s.close()


@pytest.mark.parametrize('representative_job',[None,'A'])
def test_terminal_release_intents_are_atomic_and_use_selected_thread_for_synthetic_vote(tmp_path,representative_job):
 j=RuntimeJournal(tmp_path/'j.db');r=W3ThreadRegistry(j)
 for phase,thread,job in [('trade_decision','selected',representative_job),
                          ('trade_decision:A','selected','A'),
                          ('trade_decision:B','unselected','B'),
                          ('post_decision_operations','selected','O')]:
  r.begin_stage('case',phase,thread_id=thread)
  r.save_result('case',phase,{},thread_id=thread,worker_job_id=job)
 with pytest.raises(RuntimeError,match='rollback'):
  with j.transaction() as db:
   assert 'B' in r.commit_stage(db,'case','trade_decision')
   raise RuntimeError('rollback')
 assert r.stage('case','trade_decision')['status']=='RESULT_SAVED'
 assert j.get('w3_resident_release','B') is None and j.get('w3_ack_pending','B') is None
 with j.transaction() as db:ack=r.commit_stage(db,'case','trade_decision')
 assert 'B' in ack and j.get('w3_resident_release','B') is True
 assert j.get('w3_ack_pending','B')=={'case_id':'case'}
 assert j.get('w3_resident_release','A') is None  # Operations retains its thread.
 with j.transaction() as db:assert r.commit_stage(db,'case','post_decision_operations')==['O']
 assert j.get('w3_resident_release','O') is True
 assert r.stage('case','trade_decision:A')['thread_id']=='selected'
