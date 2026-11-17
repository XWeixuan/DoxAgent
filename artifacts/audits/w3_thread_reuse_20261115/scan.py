import json,os,signal,collections,datetime
from pathlib import Path
os.nice(15);signal.alarm(100);R=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');out=[];total=0
for t in json.load(open('/tmp/w3reuse-scan.json')):
 row={'thread_id':t['thread_id'],'records':[]};raw=t['rollout_path']
 if not raw:row['missing']='index';out.append(row);continue
 path=R/raw.removeprefix('/data/')
 if not path.exists():row['missing']='file';out.append(row);continue
 row['bytes']=path.stat().st_size;row['counts']={};row['turns']=[]
 with path.open() as f:
  for line in f:
   total+=len(line)
   if total>1024*1024*1024:raise RuntimeError('byte budget')
   try:v=json.loads(line, strict=False)
   except ValueError:row['unparsed_lines']=row.get('unparsed_lines',0)+1;continue
   p=v.get('payload',{});k=v.get('type','');sub=p.get('type','') if isinstance(p,dict) else ''
   if k=='compacted' or sub in ['context_compacted','compaction_started','compaction_completed']:
    row['records'].append({'at':v.get('timestamp'),'type':k,'subtype':sub})
   if k=='event_msg' and sub in ['task_started','task_complete']:row['turns'].append({'at':v.get('timestamp'),'event':sub})
 out.append(row)
print(json.dumps({'bytes':total,'threads':out}))



