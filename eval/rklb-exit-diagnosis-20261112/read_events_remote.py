import os,sqlite3,json,re
from datetime import datetime,timezone
case='case_f8319410bef746ca998e29279eaaa216';root='trade:'+case
p=os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3');d=sqlite3.connect('file:'+p+'?mode=ro',uri=True);d.execute('PRAGMA query_only=ON');d.execute('BEGIN')
e=json.loads(d.execute('select payload from te_executions where id=?',(root,)).fetchone()[0]);account=e['account']
attempts=[json.loads(r[0]) for r in d.execute('select a.payload from te_attempts a join te_jobs j on a.job_id=j.id where j.execution_id=?',(root,))];orderids={v['order_id'] for v in attempts};previewids={i-1 for i in orderids};ids={v['id'] for v in attempts};refs={v['order_ref'] for v in attempts}
rows=[]
for rid,kind,payload,at in d.execute("select id,kind,payload,created_at from te_events where account=? and created_at>=? and created_at<? order by id",(account,'2026-11-11T15:49:30','2026-11-11T20:31:00')):
 v=json.loads(payload)
 if v.get('attempt_id') in ids or v.get('order_ref') in refs or (v.get('order_id') in orderids|previewids and v.get('client_id',82)==82) or (kind in ['sync_failure','sync_history_unavailable']):rows.append(dict(id=rid,kind=kind,at=at,value=v))
out=dict(at=datetime.now(timezone.utc).isoformat(),events=rows,profile_revision=e['profile_revision'])
print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b',r'\1\2***\3',json.dumps(out)))
