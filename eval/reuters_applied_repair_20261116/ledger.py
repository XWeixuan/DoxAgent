"""Read-only full retained solver window and source state; no browser/provider/network probes."""
import json,sqlite3,collections
from pathlib import Path
from datetime import datetime,timezone
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
keep=lambda v,ks:{k:v.get(k) for k in ks.split()}
out={'at':datetime.now(timezone.utc).isoformat(),'jobs':[],'recoveries':[],'runtimes':[]}
for (raw,) in d.execute("select data_json from datadome_solver_jobs where created_at>='2026-11-12T23:24:40' order by created_at"):
 v=json.loads(raw)
 if v.get('identity_id') in ('digitimes-de-1','reuters-backup','reuters-backup2'):out['jobs'].append(keep(v,'job_id identity_id recovery_id task_id status created_at submitted_at provider_ready_at applied_at verification_state verification_reason solver_verification cost provider_receipt application_evidence diagnostic_timeline reason business_recovery'))
for (raw,) in d.execute("select data_json from browser_recoveries where identity_id in ('digitimes-de-1','reuters-backup','reuters-backup2')"):
 v=json.loads(raw);out['recoveries'].append(keep(v,'identity_id recovery_id phase opened_at resolved_at resolved_by original_instance_id original_target_id original_allocation_id assessment last_error next_probe_at page_status'))
for (raw,) in d.execute("select data_json from identity_runtime where identity_id in ('digitimes-de-1','reuters-backup','reuters-backup2')"):
 v=json.loads(raw);out['runtimes'].append(keep(v,'identity_id instance_id operational_state recovered_at probation_until natural_success_at session_revision last_recovery_id'))
out['open_sessions']=[(i,st) for i,st in d.execute("select identity_id,state from maintenance_sessions where state='OPEN'")]
out['events']=[{'category':c,'identity':i,'purpose':p,'disposition':v,'reason':r,'n':n,'first':a,'last':b} for c,i,p,v,r,n,a,b in d.execute("select category,json_extract(payload_json,'$.identity_id'),json_extract(payload_json,'$.purpose'),json_extract(payload_json,'$.disposition'),json_extract(payload_json,'$.reason'),count(*),min(occurred_at),max(occurred_at) from access_events where site_id='reuters' and occurred_at>=? group by 1,2,3,4,5",('2026-11-16T13:07:00',))]
print(json.dumps(out,indent=2))

