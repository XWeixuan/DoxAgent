import json,sqlite3,collections,subprocess
from pathlib import Path
from datetime import datetime,timezone
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3');db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);since='2026-11-16T14:43:53Z';out={'since':since,'at':datetime.now(timezone.utc).isoformat()}
out['events']=[{'category':c,'identity':i,'purpose':p,'disposition':d,'reason':r,'n':n,'first':f,'last':l} for c,i,p,d,r,n,f,l in db.execute("select category,json_extract(payload_json,'$.identity_id'),json_extract(payload_json,'$.purpose'),json_extract(payload_json,'$.disposition'),coalesce(json_extract(payload_json,'$.reason_code'),json_extract(payload_json,'$.reason')),count(*),min(occurred_at),max(occurred_at) from access_events where site_id='reuters' and julianday(occurred_at)>=julianday(?) group by 1,2,3,4,5",(since,))]
out['recoveries']=[]
for raw, in db.execute("select data_json from browser_recoveries where identity_id in ('digitimes-de-1','reuters-backup','reuters-backup2')"):
 v=json.loads(raw);out['recoveries'].append({k:v.get(k) for k in ['identity_id','scope_id','phase','opened_at','resolved_at','automation_active','last_error','original_instance_id','original_target_id']})
out['route']=json.loads(db.execute("select data_json from site_runtime where site_id=?",('scope:source:reuters_site_search',)).fetchone()[0]) if False else None
print(json.dumps(out,indent=2))
