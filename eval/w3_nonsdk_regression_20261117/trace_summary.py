import gzip,json
from pathlib import Path
p=Path(__file__).parent;v=json.load(gzip.open(p/'logs.json.gz'))
out=[]
for t in v['traces']:
 item={k:t.get(k) for k in ('case_id','segment','started_at','finished_at','elapsed_ms','effect_attempt_count','effect_available_at','lease_token','future_done_at','future_handoff_ms','effect_created_at')}
 item['top']=[{k:e.get(k) for k in ('name','elapsed_ms','queued_ms','operation','error_type')} for e in sorted(t['events'],key=lambda x:x.get('elapsed_ms',0),reverse=True)[:5]]
 item['milestones']=t.get('milestones');out.append(item)
(p/'trace-summary.json').write_text(json.dumps(out,indent=2))
for x in out:
 if x['case_id']=='case_9f9acd13af064a67a42006f1a998b9ef':print(json.dumps(x))
