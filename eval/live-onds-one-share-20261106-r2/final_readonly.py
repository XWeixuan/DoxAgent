import os,json,sqlite3
from datetime import datetime,timezone
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import historical_profile
p=os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3');db=sqlite3.connect('file:'+p+'?mode=ro',uri=True);db.execute('BEGIN')
revision=json.loads(db.execute("select payload from runtime_values where namespace='execution_account_profiles' and key='LIVE'").fetchone()[0]);profile=historical_profile(json.loads(db.execute('select payload from te_profiles where revision=?',(revision,)).fetchone()[0])).model_copy(update={'client_id':195301})
old=[]
for root in ['trade:case_8da7542affa54cba98d711513ce8c7df','trade:case_2e138775e1fd4e159988a9682551f3aa']:
 e=json.loads(db.execute('select payload from te_executions where id=?',(root,)).fetchone()[0]);count=db.execute('select count(*) from te_attempts where job_id in(select id from te_jobs where execution_id=?)',(root,)).fetchone()[0]
 assert e['entry_result']=='FAILED' and e['group_state']=='CLOSED' and count==0
 old.append(dict(id=root,result=e['entry_result'],reason=e['entry_reason'],group=e['group_state'],attempts=count))
active=[list(r) for r in db.execute("select id,leg,state from te_jobs where state not in ('DONE','FAILED')")]
db.rollback();db.close();events=[]
broker=IbkrSession(profile,event_sink=lambda k,v:events.append((k,v)),write_guard=lambda:(_ for _ in ()).throw(AssertionError('READONLY')))
try:
 snap=broker.sync();reference='DA-ACCEPT-ONDS1-LIVE-BUY-20261106-R2'
 orders=[v for v in broker.orders.values() if v.get('order_ref')==reference]
 fills=[v for k,v in events if k=='fill' and v.get('order_ref')==reference]
 qty=snap['positions'].get(455415330,snap['positions'].get('455415330','0'))
 assert snap['history_complete'];assert len(orders)==1 and orders[0]['status']=='Filled';assert len({v['exec_id'] for v in fills})==1 and all(v['side']=='BUY' and str(v['quantity'])=='1' and v['con_id']==455415330 for v in fills);assert str(qty)=='1'
 result=dict(at=datetime.now(timezone.utc).isoformat(),orders_submitted=0,verified_live_test_orders=orders,verified_live_test_fills=fills,onds_quantity=qty,complete=snap['complete'],history_complete=snap['history_complete'],account_ready=snap['account_values'].get('AccountReady'),settled_cash=snap['account_values'].get('SettledCash'),fees=[v for k,v in events if k=='fees' and v.get('exec_id') in {f['exec_id'] for f in fills}],sync_at=snap['at'],stage_seconds=snap['stage_seconds'],old=old,active_jobs=active)
finally:broker.close()
print(__import__('re').sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b',r'\1\2***\3',json.dumps(result)))
