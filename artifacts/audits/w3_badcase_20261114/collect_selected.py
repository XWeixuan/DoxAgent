import json,sqlite3,os,signal
from pathlib import Path
from doxagent.v2_read.native_content import NativeContent
os.nice(15);signal.alarm(90)
ROOT=Path('/data')
def db(rel):
 c=sqlite3.connect((ROOT/rel).as_uri()+'?mode=ro',uri=True,timeout=3);c.execute('pragma query_only=ON');c.row_factory=sqlite3.Row;return c
d=db('runtime/runtime.sqlite3');w=db('workspaces/worker-jobs.sqlite3');s=db('codex-home/state_5.sqlite');codec=NativeContent(ROOT/'runtime/runtime.sqlite3')
ids=['case_6db274e8e6ce40bb96a4aabc55c11b8d','case_084bf71f990c45bcb9553241d0103a7c','case_1b18423c38084f97931c3fe052d3dfd2','case_f8319410bef746ca998e29279eaaa216','case_e1327a00f9dc41148ae8a2623042a687','case_a348daceb87f466fb0be11d3e3f607b9','case_1b8164310214483a850e604123c130cd','case_5960bdaac0b74e80bbc2557cb08e65fb','case_7e4ad7125b9249aa80545ca8074524be']
out=[]
for cid in ids:
 row=d.execute('select * from runtime_v2_cases where case_id=?',(cid,)).fetchone()
 if not row:continue
 c={'case_id':cid,'row':{k:row[k] for k in row.keys() if k!='payload_json'}}
 c['case']=codec.decode(json.loads(row['payload_json']))
 wr=d.execute('select payload_json from runtime_v2_w3_cases where case_id=?',(cid,)).fetchone();c['w3']=codec.decode(json.loads(wr[0])) if wr else None
 c['stages']=[dict(v) for v in d.execute('select * from runtime_v2_w3_stages where case_id=?',(cid,))]
 for st in c['stages']:
  j=w.execute('select payload,request from jobs where id=?',(st['worker_job_id'],)).fetchone()
  if j:c.setdefault('jobs',[]).append({'phase':st['phase'],'payload':json.loads(j[0]),'request':json.loads(j[1]) if j[1] else None})
  th=s.execute('select * from threads where id=?',(st['thread_id'],)).fetchone()
  if th:c.setdefault('threads',[]).append(dict(th))
  reg=w.execute('select * from w3_thread_registry where thread_id=?',(st['thread_id'],)).fetchone()
  if reg:c.setdefault('registry',[]).append(dict(reg))
 c['effects']=[codec.decode(json.loads(v[0])) for v in d.execute('select payload_json from runtime_v2_effects where case_id=?',(cid,))]
 out.append(c)
sources={c['row']['source_message_id'] for c in out[:4]}
siblings=[]
for mid in sources:
 for r in d.execute('select case_id,ticker,source_message_id,created_at,payload_json from runtime_v2_cases where source_message_id=?',(mid,)):
  x=dict(r);v=json.loads(x.pop('payload_json'));x['source']=codec.decode(v.get('source',{}));wr=d.execute('select payload_json from runtime_v2_w3_cases where case_id=?',(x['case_id'],)).fetchone();x['w3']=codec.decode(json.loads(wr[0])) if wr else None;siblings.append(x)
print(json.dumps({'cases':out,'same_message_cases':siblings},ensure_ascii=False))
