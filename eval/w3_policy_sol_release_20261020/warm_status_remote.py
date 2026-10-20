import json,sqlite3,os
from pathlib import Path
db=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True)
out={'release':os.environ.get('DOXAGENT_BUILD_COMMIT'),'slots':db.execute("SELECT json_extract(config,'$.profile.model'),state,count(*) FROM w3_ready_slots GROUP BY 1,2").fetchall(),'jobs':[]}
db.row_factory=sqlite3.Row
out['operations']=[{k:r[k] for k in ['operation_key','status','step','thread_id','error_code','error_message','updated_at'] if k in r.keys()} for r in db.execute('SELECT * FROM w3_thread_operations ORDER BY rowid DESC LIMIT 8')]
home=sqlite3.connect('file:'+os.environ['CODEX_HOME']+'/state_5.sqlite?mode=ro',uri=True)
out['live_slots']=[]
for identity,config,state,thread,case,error in db.execute("SELECT id,config,state,thread_id,case_id,error FROM w3_ready_slots WHERE state IN ('READY','BOUND','BUILDING')"):
 item={'slot':identity,'profile':json.loads(config)['profile'],'state':state,'thread_id':thread,'case_id':case,'error':error}
 if thread:
  row=home.execute('SELECT rollout_path FROM threads WHERE id=?',(thread,)).fetchone()
  if row and Path(row[0]).exists():
   records=[json.loads(l) for l in Path(row[0]).read_text().splitlines()]
   settings=[r['payload']['thread_settings'] for r in records if r.get('payload',{}).get('type')=='thread_settings_applied']
   contexts=[r['payload'] for r in records if r['type']=='turn_context']
   item['settings']=[{k:s.get(k) for k in ('model','service_tier','reasoning_effort')} for s in settings[-1:]]
   item['contexts']=[{k:s.get(k) for k in ('model','effort','turn_id')} for s in contexts[-1:]]
 item['last_at']=records[-1].get('timestamp') if thread and row and Path(row[0]).exists() else None
 out['live_slots'].append(item)
for payload,request in db.execute('SELECT payload,request FROM jobs ORDER BY sequence DESC LIMIT 60'):
 j=json.loads(payload);r=json.loads(request) if request else {}
 if r.get('task_phase'):
  out['jobs'].append({k:j.get(k) for k in ['job_id','status','error_code','error_message','created_at','thread_id','turn_id']}|{k:r.get(k) for k in ['task_phase','model','effort','ticker']})
print(json.dumps(out))
