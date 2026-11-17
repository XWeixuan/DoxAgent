"""Local deterministic check of dual Future writes after Effect ownership changes."""
import asyncio,json,sqlite3,tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,UTC,timedelta
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
from doxagent.persistent_runtime_v2.schema import RuntimeEffect
from doxagent.persistent_runtime_v2.fencing import write_scope,assert_write_lease
from doxagent.persistent_runtime_v2.journal import LeaseLost
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder)/'r.db';until=(datetime.now(UTC)+timedelta(minutes=2)).isoformat()
 with closing(sqlite3.connect(p)) as db, db:
  db.execute('create table runtime_v2_effects(effect_id text,status text,payload_json text)')
  db.execute('insert into runtime_v2_effects values(?,?,?)',('e','RUNNING',json.dumps(dict(lease_token=1,lease_until=until))))
 r=object.__new__(CodexW3AgentRunner);r._journal=SimpleNamespace(path=p);r._wait_v4=lambda coro,**kw:asyncio.run(coro)
 async def phase(**kw):
  kw['shared_trade']['ready'].set()
  with closing(sqlite3.connect(p)) as db, db:db.execute("update runtime_v2_effects set payload_json=json_set(payload_json,'$.lease_token',2)")
  return dict(decision='NO_TRADE'),SimpleNamespace(job_id=kw['record']['id'])
 r._run_phase=phase;saved=[]
 def save(label,result,job):
  with closing(sqlite3.connect(p)) as db, db:assert_write_lease(db)
  saved.append(label)
 effect=RuntimeEffect(effect_id='e',case_id='c',effect_type='route_to_w3',idempotency_key='c',lease_token=1)
 try:
  with write_scope('effect',effect,lambda:datetime.now(UTC)):
   r.run_trade_pair(case=SimpleNamespace(case_id='c'),records=dict(A={'id':'a'},B={'id':'b'}),payload={},context=None,research=None,save=save)
 except LeaseLost:old_rejected=True
 else:old_rejected=False
 with write_scope('effect',effect.model_copy(update={'lease_token':2}),lambda:datetime.now(UTC)):
  for b in ('A','B'):save(b,{'decision':'NO_TRADE'},SimpleNamespace(job_id=b))
 print(json.dumps(dict(old_future_save_rejected=old_rejected,current_owner_can_save=saved,model_calls=0)))

