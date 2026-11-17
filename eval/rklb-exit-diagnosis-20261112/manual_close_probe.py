import sqlite3,json,re
from datetime import datetime,timezone
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import historical_profile
d=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True)
d.row_factory=sqlite3.Row;d.execute('pragma query_only=ON')
rev=json.loads(d.execute("select payload from runtime_values where namespace='execution_account_profiles' and key='LIVE'").fetchone()[0])
p=historical_profile(json.loads(d.execute('select payload from te_profiles where revision=?',(rev,)).fetchone()[0])).model_copy(update={'client_id':195633})
lots=[json.loads(r[0]) for r in d.execute('select payload from te_lots where account=?',(p.expected_account_id,)) if json.loads(r[0]).get('remaining_qty')]
roots={l['execution_id'] if 'execution_id' in l else l['id'].removesuffix(':hedge') for l in lots}
jobs=[json.loads(r[0]) for r in d.execute('select payload from te_jobs where account=?',(p.expected_account_id,)) if json.loads(r[0])['execution_id'] in roots]
executions=[json.loads(r[0]) for r in d.execute('select payload from te_executions where account=?',(p.expected_account_id,)) if json.loads(r[0])['id'] in roots]
attempts=[json.loads(r[0]) for r in d.execute("select payload from te_attempts where account=? and state not in ('SETTLED','NOT_SENT')",(p.expected_account_id,))]
events=[];opened=[]
def deny(*a,**kw):raise AssertionError('READ_ONLY')
b=IbkrSession(p,event_sink=lambda k,v:events.append((k,v)),write_guard=deny)
try:
 b.connect();b.app.placeOrder=deny;b.app.cancelOrder=deny
 original=b.app.openOrder
 def observe(oid,c,o,s):
  if o.account==p.expected_account_id:opened.append(dict(order_id=oid,client_id=o.clientId,order_ref=o.orderRef,con_id=c.conId,symbol=c.symbol,side=o.action,quantity=str(o.totalQuantity),status=s.status))
  original(oid,c,o,s)
 b.app.openOrder=observe;snap=b.sync();opened.clear();b.open_event.clear();b.app.reqAllOpenOrders();assert b.open_event.wait(5)
 out=dict(at=datetime.now(timezone.utc).isoformat(),account=p.expected_account_id,profile_revision=rev,lots=lots,jobs=jobs,executions=executions,unsettled_attempts=attempts,positions=snap['positions'],open_orders=opened,complete=snap['complete'],history_complete=snap['history_complete'],fills=[v for k,v in events if k=='fill'],baselines=[dict(r) for r in d.execute("select key,payload from runtime_values where namespace='execution_position_baselines' and key like ?",(p.expected_account_id+':%',))],orders_submitted=0)
finally:b.close();d.close()
print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b',r'\1\2***\3',json.dumps(out)))
