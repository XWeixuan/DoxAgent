import json,sqlite3,collections
from pathlib import Path
from datetime import datetime,timezone
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('pragma query_only=on')
def pick(v,keys):return {k:v.get(k) for k in keys.split()}
ids={'digitimes-de-1','reuters-backup'}
res={'at':datetime.now(timezone.utc).isoformat(),'jobs':[],'recoveries':[]}
for row in d.execute("select data_json from datadome_solver_jobs where julianday(created_at)>=julianday('2026-11-06T21:51:52Z')"):
 v=json.loads(row[0])
 if v.get('identity_id') in ids:res['jobs'].append(pick(v,'job_id recovery_id identity_id status task_id created_at submitted_at provider_ready_at applied_at provider_cost cost error_code last_error verification_state verification_reason solver_verification recurrence_at diagnostics diagnostic_timeline'))
for row in d.execute('select data_json from browser_recoveries'):
 v=json.loads(row[0])
 if v.get('identity_id') in ids:res['recoveries'].append(pick(v,'recovery_id identity_id phase opened_at resolved_at resolved_by last_error assessment document_recovery scene_preparation'))
res['events']=[]
for row in d.execute("select occurred_at,category,payload_json from access_events where site_id='reuters' and julianday(occurred_at)>=julianday('2026-11-06T21:51:52Z') and category not in ('ACCESS_RESULT','CAPTURE_CONSUMED') order by occurred_at"):
 v=json.loads(row['payload_json']);res['events'].append({'at':row['occurred_at'],'category':row['category'],**pick(v,'identity_id recovery_id result reason reason_code stage probe_id')})
print(json.dumps(res,indent=2));d.close()
