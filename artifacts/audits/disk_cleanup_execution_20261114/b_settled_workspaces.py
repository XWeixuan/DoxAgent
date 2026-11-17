import json,sqlite3,time,os
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.workspace_retention import collect
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.codex_worker.job_store import JobStore
os.nice(10);s=DoxAgentSettings();w=Path('/data/workspaces');cut=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat();roots=set()
with sqlite3.connect(Path(s.codex_runtime_sqlite_path).as_uri()+'?mode=ro',uri=True) as db:
 for run, in db.execute("SELECT run_id FROM codex_document3_v21_runs WHERE run_id LIKE 'runtime-maintain-%' AND json_extract(payload,'$.settlement.at')<?",(cut,)):
  marker=w/run/'audit/retained-assets.json'
  if marker.is_file() and json.loads(marker.read_bytes()).get('complete'):continue
  roots.add(run)
with sqlite3.connect((w/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
 ids=[r[0] for r in db.execute("SELECT DISTINCT json_extract(payload,'$.run_id') FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id') LIKE 'runtime-maintain-%'") if r[0] in roots or r[0].removesuffix('-maintain') in roots]
store=JobStore.__new__(JobStore);store.root=w;store.path=w/'worker-jobs.sqlite3';jobs=SimpleNamespace(store=store);ws=LocalWorkspaceStore(w);out={'candidate_roots':len(roots),'candidate_job_runs':len(ids),'reports':[]};print(json.dumps({'stage':'preflight','roots':len(roots),'job_runs':len(ids)}),flush=True)
for i in range(0,len(ids),128):
 try:
  report=collect(ws,jobs,s,run_ids=ids[i:i+128],limit=256);out['reports'].append(report);print(json.dumps({'stage':'batch','offset':i,'retired':len(report['runs']),'allocated_removed':sum(r.get('allocated',0) for r in report['runs']),'errors':report['errors'][:2]}),flush=True)
 except RuntimeError as e:out.setdefault('batch_errors',[]).append({'offset':i,'error':str(e)})
print(json.dumps({'result':out}),flush=True)
