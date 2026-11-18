"""Scalar indexed metadata only; time-bounded pages, no business input expansion."""
import json,sqlite3,time,subprocess
from pathlib import Path
from datetime import datetime,UTC
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out={'at':datetime.now(UTC).isoformat(),'slots':[],'operations':[],'errors':[]}
c=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-codex-worker-1']))[0];out['worker_start']=c['State']['StartedAt'];out['worker_commit']=c['Config']['Labels'].get('org.opencontainers.image.revision')
def connect(path):
 d=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.15);d.row_factory=sqlite3.Row;d.execute('pragma query_only=ON');d.execute('pragma cache_size=-2048');return d
with connect(root/'workspaces/worker-jobs.sqlite3') as d:
 cursor=2**63-1
 for _ in range(8):
  deadline=time.monotonic()+1;d.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  try:page=[dict(x) for x in d.execute('SELECT rowid,id,scope,state,created_at,bound_at,ready_at,failed_at,retire_checked_at,retire_next_at,retire_error,claim_kind,case_id,record_id,capsule_id,generation,error FROM w3_ready_slots WHERE rowid<? ORDER BY rowid DESC LIMIT 128',(cursor,))]
  except sqlite3.OperationalError as e:out['errors'].append(str(e));break
  if not page:break
  out['slots']+=page;cursor=page[-1]['rowid']
  if page[-1]['created_at'] and page[-1]['created_at']<out['worker_start']:break
 d.set_progress_handler(None,0)
 for s in out['slots']:
  if not s['created_at'] or s['created_at']<out['worker_start']:continue
  r=d.execute('SELECT operation_key,created_at,updated_at,step,status,fork_dispatched_at,startup_attempts,startup_receipt FROM w3_thread_operations WHERE operation_key=?',('w3-slot-fork:'+s['id'],)).fetchone()
  if r:
   v=dict(r);v['startup_receipt']=json.loads(v['startup_receipt']) if v['startup_receipt'] else None;out['operations'].append(v)
 out['asset_counts']=[dict(x) for x in d.execute('SELECT state,count(*) n,sum(retained_until IS NULL) null_watermarks FROM w3_asset_builds GROUP BY state')]
 out['old_contexts']=[dict(x) for x in d.execute('SELECT count(*) n FROM w3_case_contexts')]
with connect(root/'runtime/runtime.sqlite3') as d:
 out['new_values']=[dict(x) for x in d.execute("SELECT namespace,count(*) n FROM runtime_values WHERE namespace IN ('w1_library_bridge','w1_prepared_index') GROUP BY namespace")]
 out['case_trigger']=d.execute("SELECT sql FROM sqlite_master WHERE name='v2_capture_runtime_v2_cases_UPDATE'").fetchone()[0]
print(json.dumps(out))
