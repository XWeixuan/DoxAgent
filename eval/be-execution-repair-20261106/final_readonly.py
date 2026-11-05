import os,json,sqlite3
from datetime import datetime,timezone
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import historical_profile
p=os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3');db=sqlite3.connect('file:'+p+'?mode=ro',uri=True);db.execute('BEGIN')
revision=json.loads(db.execute("select payload from runtime_values where namespace='execution_account_profiles' and key='LIVE'").fetchone()[0]);profile=historical_profile(json.loads(db.execute('select payload from te_profiles where revision=?',(revision,)).fetchone()[0])).model_copy(update={'client_id':195271})
old=[]
for root in ['trade:case_8da7542affa54cba98d711513ce8c7df','trade:case_2e138775e1fd4e159988a9682551f3aa']:
 e=json.loads(db.execute('select payload from te_executions where id=?',(root,)).fetchone()[0]);count=db.execute('select count(*) from te_attempts where job_id in(select id from te_jobs where execution_id=?)',(root,)).fetchone()[0]
 assert e['entry_result']=='FAILED' and e['group_state']=='CLOSED' and count==0
 old.append(dict(id=root,result=e['entry_result'],reason=e['entry_reason'],group=e['group_state'],attempts=count))
active=[list(r) for r in db.execute("select id,leg,state from te_jobs where state not in ('DONE','FAILED')")]
db.rollback();db.close();events=[]
broker=IbkrSession(profile,event_sink=lambda k,v:events.append((k,v)),write_guard=lambda:(_ for _ in ()).throw(AssertionError('READONLY')))
try:
 snap=broker.sync();reference='DA-ACCEPT-ONDS1-LIVE-BUY-20261106'
 orders=[v for v in broker.orders.values() if v.get('order_ref')==reference]
 fills=[v for k,v in events if k=='fill' and v.get('order_ref')==reference]
 qty=snap['positions'].get(455415330,snap['positions'].get('455415330','0'))
 assert len(orders)==1 and orders[0]['status'] in {'Cancelled','Inactive'} and not fills and str(qty)=='0'
 result=dict(at=datetime.now(timezone.utc).isoformat(),orders_submitted=0,verified_live_test_orders=orders,verified_live_test_fills=fills,onds_quantity=qty,complete=snap['complete'],sync_at=snap['at'],stage_seconds=snap['stage_seconds'],old=old,active_jobs=active)
finally:broker.close()
print(json.dumps(result))
