import os,sqlite3,json,re
from datetime import datetime,timezone
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import historical_profile
case='trade:case_f8319410bef746ca998e29279eaaa216';p=os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3');d=sqlite3.connect('file:'+p+'?mode=ro',uri=True)
e=json.loads(d.execute('select payload from te_executions where id=?',(case,)).fetchone()[0]);profile=historical_profile(json.loads(d.execute('select payload from te_profiles where revision=?',(e['profile_revision'],)).fetchone()[0])).model_copy(update={'client_id':195612})
refs={json.loads(r[0])['order_ref'] for r in d.execute('select a.payload from te_attempts a join te_jobs j on a.job_id=j.id where j.execution_id=?',(case,))};d.close();events=[]
def deny(*a,**kw):raise AssertionError('READONLY_NO_ORDER_OR_WHATIF')
b=IbkrSession(profile,event_sink=lambda k,v:events.append((k,v)),write_guard=deny)
try:
 b.connect();b.app.placeOrder=deny;b.app.cancelOrder=deny;s=b.sync()
 fills=[v for k,v in events if k=='fill' and v.get('order_ref') in refs]
 orders=[v for v in b.orders.values() if v.get('order_ref') in refs]
 out=dict(at=datetime.now(timezone.utc).isoformat(),orders_submitted=0,complete=s['complete'],history_complete=s.get('history_complete'),positions={str(c):s['positions'].get(c,s['positions'].get(str(c),'0')) for c in [787273575,72539702]},orders=orders,fills=fills,fees=[v for k,v in events if k=='fees' and v.get('exec_id') in {f['exec_id'] for f in fills}],errors=b.errors)
finally:b.close()
print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b',r'\1\2***\3',json.dumps(out)))
