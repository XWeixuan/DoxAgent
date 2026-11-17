import json,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,timezone
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3');c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);out={'at':datetime.now(timezone.utc).isoformat(),'recoveries':[]}
out['scope']=json.loads(c.execute("select data_json from source_access_scopes where scope_id='source:reuters_site_search'").fetchone()[0])
for raw, in c.execute("select data_json from browser_recoveries where identity_id in ('digitimes-de-1','reuters-backup','reuters-backup2')"):
 v=json.loads(raw);out['recoveries'].append({k:v.get(k) for k in ['identity_id','scope_id','phase','automation_active','original_target_id','original_instance_id','last_error']})
out['after_rollback_events']=[{'identity':i,'purpose':p,'result':r,'reason':s,'n':n,'last_at':t} for i,p,r,s,n,t in c.execute("select json_extract(payload_json,'$.identity_id'),json_extract(payload_json,'$.purpose'),json_extract(payload_json,'$.disposition'),coalesce(json_extract(payload_json,'$.reason_code'),json_extract(payload_json,'$.reason')),count(*),max(occurred_at) from access_events where site_id='reuters' and category='ACCESS_RESULT' and julianday(occurred_at)>=julianday('2026-11-16T15:07:08Z') group by 1,2,3,4")]
out['jobs_total_since_baseline']=c.execute("select count(*) from datadome_solver_jobs where created_at>='2026-11-12T23:24:40' and json_extract(data_json,'$.task_id') is not null").fetchone()[0]
print(json.dumps(out,indent=2))
# Remove only this task's unused failed relay configuration, never Chrome Profiles.
if subprocess.run(['docker','inspect','doxagent-reuters-backup2-egress'],capture_output=True).returncode:
 proxy=Path('/opt/doxagent-egress-clash/reuters-backup2');assert str(proxy.resolve())=='/opt/doxagent-egress-clash/reuters-backup2'
 for name in ['config.yaml','fresh-node.yaml']:(proxy/name).unlink(missing_ok=True)
 if proxy.exists() and not any(proxy.iterdir()):proxy.rmdir()
