import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
def keep(v,keys):return {k:v.get(k) for k in keys.split()}
out={}
for table in ('browser_recoveries','datadome_solver_jobs','challenge_tasks'):
 rows=[]
 for r in d.execute('select data_json from '+table):
  v=json.loads(r[0])
  if v.get('identity_id') not in ('digitimes-de-1','reuters-backup'):continue
  if table!='browser_recoveries' and max(v.get('created_at',''),v.get('updated_at',''))<'2026-11-12T22:07':continue
  rows.append(keep(v,'identity_id recovery_id task_id job_id phase status state created_at updated_at opened_at next_probe_at last_error reason assessment attempt_count resolved_at resolved_by applied_at verification_state verification_reason solver_verification diagnostics diagnostic_timeline document_recovery scene_preparation'))
 out[table]=rows
print(json.dumps(out,indent=2))
