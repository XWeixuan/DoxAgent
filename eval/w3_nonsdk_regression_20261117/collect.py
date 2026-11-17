"""Bounded metadata cohort; read-only, short indexed queries, no full Case inputs."""
import json,sqlite3,time
from pathlib import Path
from datetime import datetime,UTC
base=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def connect(p):
 d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row;d.execute('pragma query_only=ON');d.execute('pragma cache_size=-4096');return d
r=connect(base/'runtime/runtime.sqlite3');w=connect(base/'workspaces/worker-jobs.sqlite3');v=connect(base/'read/serving-v4-20261018/v2.sqlite3')
candidates=r.execute("select case_id,ticker,status,created_at,json_extract(payload_json,'$.runtime_mode') mode,json_extract(payload_json,'$.w3_contract_version') contract from runtime_v2_cases order by rowid desc limit 2500").fetchall()
out=dict(at=datetime.now(UTC).isoformat(),candidate_count=len(candidates),oldest_candidate=candidates[-1]['created_at'],cases=[])
for i,c in enumerate(candidates):
 if c['created_at']<'2026-11-16T11:06' or c['mode']!='REALTIME':continue
 cid=c['case_id'];stages=[dict(x) for x in r.execute("select phase,status,thread_id,worker_job_id,accepted_at,committed_at,error,json_extract(input_refs,'$.workspace') workspace,json_extract(input_refs,'$.record_id') record_id,json_extract(input_refs,'$.duration_ms') duration_ms from runtime_v2_w3_stages where case_id=?",(cid,))]
 if not stages:continue
 x=dict(c);x['stages']=stages
 x['effects']=[dict(z) for z in r.execute("select effect_id,effect_type,status,updated_at,json_extract(payload_json,'$.created_at') created_at,json_extract(payload_json,'$.payload.decision_effective_at') effective_at from runtime_v2_effects where case_id=? and effect_type in ('route_to_w3','w3_post_decision')",(cid,))]
 x['attempts']=[dict(json.loads(z[0]),object_id=z[1]) for z in v.execute("select payload,id from object_current where kind='attempt' and ticker=? and parent=?",(c['ticker'],cid))]
 s=v.execute("select payload from object_current where kind='case' and ticker=? and id=?",(c['ticker'],cid)).fetchone();x['ui_case']=json.loads(s[0]) if s else None
 x['rounds']=[dict(z) for z in r.execute("select turn_id,lane,round_name,attempt_number,status,json_extract(payload_json,'$.started_at') started_at,json_extract(payload_json,'$.finished_at') finished_at,json_extract(payload_json,'$.model_interval') model_interval from runtime_v2_turns where case_id=?",(cid,))]
 runs={z[0] for st in stages for z in r.execute("select workspace from runtime_v2_w3_threads where record_id=?",(st['record_id'],))}
 jobs={}
 for run in runs:
  for j in w.execute("select id,status,json_extract(request,'$.task_phase') phase,json_extract(payload,'$.run_id') run,json_extract(payload,'$.created_at') created,json_extract(payload,'$.started_at') started,json_extract(payload,'$.finished_at') finished,json_extract(payload,'$.resource_receipt.execution_timing') timing,json_extract(payload,'$.error_code') error,json_extract(payload,'$.error_message') detail from jobs INDEXED BY jobs_run_owner where json_extract(payload,'$.run_id')=? and json_extract(payload,'$.attempt_id') like ?",(run,cid[-24:]+'-%')):
   z=dict(j);z['timing']=json.loads(z['timing']) if z['timing'] else {};jobs[z['id']]=z
 x['jobs']=list(jobs.values());out['cases'].append(x)
 if i%20==0:time.sleep(.02)
print(json.dumps(out))
