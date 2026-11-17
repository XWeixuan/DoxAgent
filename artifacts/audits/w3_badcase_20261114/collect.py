import json,sqlite3,os,signal
from pathlib import Path
os.nice(15);signal.alarm(60)
ROOT=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def db(rel):
 c=sqlite3.connect((ROOT/rel).as_uri()+'?mode=ro',uri=True,timeout=2);c.execute('pragma query_only=ON');c.row_factory=sqlite3.Row;return c
d=db('runtime/runtime.sqlite3');w=db('workspaces/worker-jobs.sqlite3');s=db('codex-home/state_5.sqlite')
need=['Fermi does 600MW','Nebius Shares Drop 15%','PT Raised to $85 at Berenberg','Get Ready for What']
out=[]
rows=d.execute("select case_id,ticker,created_at,json_extract(payload_json,'$.source.title') title from runtime_v2_cases order by rowid desc limit 15000").fetchall()
for r in rows:
 if not any(n.casefold() in (r['title'] or '').casefold() for n in need):continue
 c=dict(r);cid=c['case_id'];c['case']=json.loads(d.execute('select payload_json from runtime_v2_cases where case_id=?',(cid,)).fetchone()[0])
 wr=d.execute('select payload_json from runtime_v2_w3_cases where case_id=?',(cid,)).fetchone();c['w3']=json.loads(wr[0]) if wr else None
 c['stages']=[dict(v) for v in d.execute('select * from runtime_v2_w3_stages where case_id=?',(cid,))]
 for st in c['stages']:
  for k in ['result_json','input_refs']:
   if st[k]:st[k]=json.loads(st[k])
  j=w.execute('select payload,request from jobs where id=?',(st['worker_job_id'],)).fetchone()
  if j:c.setdefault('jobs',[]).append({'phase':st['phase'],'payload':json.loads(j[0]),'request':json.loads(j[1]) if j[1] else None})
  th=s.execute('select * from threads where id=?',(st['thread_id'],)).fetchone()
  if th:
   c.setdefault('threads',[]).append(dict(th))
  reg=w.execute('select * from w3_thread_registry where thread_id=?',(st['thread_id'],)).fetchone()
  if reg:c.setdefault('registry',[]).append(dict(reg))
 c['effects']=[json.loads(v[0]) for v in d.execute('select payload_json from runtime_v2_effects where case_id=?',(cid,))]
 out.append(c)
print(json.dumps({'scanned':len(rows),'cases':out},ensure_ascii=False))
