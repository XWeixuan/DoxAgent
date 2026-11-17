import json,sqlite3,os,signal
from pathlib import Path
os.nice(15);signal.alarm(90)
R=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');sel=json.load(open('/tmp/w3-ops-retained-20261112.json'))
def db(p):return sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
d=db(R/'runtime/runtime.sqlite3');w=db(R/'workspaces/worker-jobs.sqlite3');state=db(R/'codex-home/state_5.sqlite');out=[];total=0
for c in sel['cases']:
 j=json.loads(w.execute('select payload from jobs where id=?',(c['job_id'],)).fetchone()[0]);s=d.execute("select thread_id,result_json from runtime_v2_w3_stages where case_id=? and phase='post_decision_operations'",(c['case_id'],)).fetchone()
 c.update(thread_id=s[0],job_meta={k:j.get(k) for k in ['created_at','started_at','finished_at','execution_phase','model','reasoning_effort','telemetry']},result=json.loads(s[1]) if s[1] else None)
 cr=d.execute("select ticker,json_extract(payload_json,'$.source.title') from runtime_v2_cases where case_id=?",(c['case_id'],)).fetchone();c.update(ticker=cr[0],title=cr[1])
 row=state.execute('select rollout_path from threads where id=?',(s[0],)).fetchone();c['items']=[]
 if not row:c['missing']='thread_index';out.append(c);continue
 path=Path(row[0]);path=R/str(path).removeprefix('/data/') if str(path).startswith('/data/') else path
 if not path.exists():c['missing']='rollout_file';out.append(c);continue
 c['rollout_path']=str(row[0]);c['rollout_bytes']=path.stat().st_size
 start=j['started_at'][:19];end=j['finished_at'][:19];counts={}
 with path.open() as f:
  for line in f:
   total+=len(line)
   if total>160*1024*1024:raise RuntimeError('byte budget')
   v=json.loads(line);ts=v.get('timestamp','')[:19]
   if ts<start or ts>end:continue
   p=v.get('payload',{});typ=v.get('type');sub=p.get('type','');counts[typ+':'+sub]=counts.get(typ+':'+sub,0)+1
   if typ=='response_item':
    item={k:p[k] for k in ['type','role','name','call_id','status','summary'] if k in p};item['at']=v['timestamp']
    for k in ['content','arguments','input','output']:
     if k in p:item[k]=str(p[k])[:2200]
    c['items'].append(item)
   elif typ=='event_msg' and sub in ['token_count','task_started','task_complete','error','warning','context_compacted']:
    c['items'].append({'at':v['timestamp'],'event':sub,'data':p if sub=='token_count' else str(p)[:1200]})
 c['counts']=counts;out.append(c)
print(json.dumps({'bytes_read':total,'cases':out},ensure_ascii=False))


