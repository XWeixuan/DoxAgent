from pathlib import Path
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.worker import WriterLock
from doxagent.trade_execution import groups

assert out['complete'] and out['history_complete'] and not out['open_orders'] and not out['unsettled_attempts']
ROOT='trade:case_6db274e8e6ce40bb96a4aabc55c11b8d'
expected={ROOT:(584127832,6,'BUY','0000e95f.6af6e960.01.01'),ROOT+':hedge':(73340487,7,'SELL','0002bd06.6af6c443.01.01')}
assert {l['id'] for l in out['lots']}==set(expected), 'LIVE_LOTS_CHANGED'
for lot in out['lots']:
 cid,qty,side,eid=expected[lot['id']]
 assert lot['con_id']==cid and lot['remaining_qty']==qty and str(out['positions'].get(cid,out['positions'].get(str(cid),'0')))=='0'
 assert any(f['exec_id']==eid and f['con_id']==cid and f['side']==side and int(f['quantity'])==qty for f in out['fills'])
path=Path('/data/runtime/runtime.sqlite3')
with WriterLock(path):
 journal=RuntimeJournal(path,initialize=False)
 repo=ExecutionRepository(journal,initialize=False)
 backup=Path('/data/runtime/acceptance/manual-close-cohr-20261113')
 backup.mkdir(parents=True,exist_ok=True,mode=0o700)
 with (backup/'before.json').open('x') as f:json.dump(out,f,default=str)
 for identity,(cid,qty,side,eid) in expected.items():
  lot=repo.require('lots',identity)
  assert lot['remaining_qty']==qty
  fill=next(f for f in out['fills'] if f['exec_id']==eid)
  repo.adjust_lot(identity,adjustment_id='manual-close-20261113:'+eid,quantity_delta=-qty,effective_at=fill['time'],reason='User-authorized manual full close; broker execId='+eid+'; side='+side+'; price='+fill['price'])
 for identity in expected:
  job=repo.require('jobs',identity+':exit')
  assert not repo.require('lots',identity)['remaining_qty']
  repo.finish(job,'MANUALLY_CLOSED_BROKER_VERIFIED',complete=True)
 groups.advance(repo,ROOT)
 execution=repo.require('executions',ROOT)
 jobs=[repo.require('jobs',identity+':exit') for identity in expected]
 assert execution['group_state']=='CLOSED' and all(j['state']=='DONE' for j in jobs)
 with repo.transaction(write=False) as db:
  active=db.execute("select count(*) from te_jobs where execution_id=? and state not in ('DONE','FAILED')",(ROOT,)).fetchone()[0]
 assert active==0
 result=dict(at=datetime.now(timezone.utc).isoformat(),group_id=ROOT,group_state=execution['group_state'],leg_quantities=execution['leg_quantities'],exit_jobs=[{k:j.get(k) for k in ['id','state','reason','result']} for j in jobs],active_jobs=active,orders_submitted=0)
 (backup/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 print(json.dumps(result))
