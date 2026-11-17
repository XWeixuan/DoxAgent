import os,json,sqlite3,hashlib,time
from pathlib import Path
from collections import defaultdict,Counter
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.initialization_owners import control_paths
os.nice(10);s=DoxAgentSettings();w=Path('/data/workspaces');targets={p.name for p in (w/'.init').iterdir() if p.is_dir() and not (w/'.init-owners'/(p.name+'.json')).exists()};out={'unowned_count':len(targets),'matches':{},'errors':[]};attempts=[];runinfo={};names={p.name for p in w.iterdir() if p.is_dir() and p.name.startswith(('init-','d2ws-','rerun-'))}
for ledger in control_paths(s):
 with sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True) as db:
  rows=db.execute("SELECT id,ticker,json_extract(payload,'$.status'),json_extract(payload,'$.manual_resume_required'),json_extract(payload,'$.updated_at') FROM initialization_runs").fetchall()
  for r in rows:runinfo[(str(ledger),r[0])]={'ticker':r[1],'status':r[2],'manual':r[3],'at':r[4]}
  attempts.extend((execution,parent,str(ledger)) for execution,parent in db.execute('SELECT execution_id,run_id FROM initialization_attempts') if (str(ledger),parent) in runinfo)
with sqlite3.connect((w/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
 names.update(r[0] for r in db.execute("SELECT DISTINCT json_extract(payload,'$.run_id') FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id') LIKE 'init-%' OR json_extract(payload,'$.run_id') LIKE 'd2ws-%' OR json_extract(payload,'$.run_id') LIKE 'rerun-%'") if r[0])
for identity in targets:
 for f in list((w/'.init'/identity/'audit/jobs').glob('*.json'))[:2]:
  try:
   if f.stat().st_size>2*2**20:continue
   v=json.loads(f.read_bytes());r=v.get('run_id') or v.get('job',{}).get('run_id')
   if isinstance(r,str):names.add(r)
  except Exception:pass
byparent=defaultdict(list)
for a in attempts:byparent[a[1]].append(a)
start=time.monotonic();checks=0
for name in names:
 parents=[p for p in byparent if name==p or name.startswith(p+'-')]
 candidates=byparent[max(parents,key=len)] if parents else attempts
 for snapshot,parent,ledger in candidates:
  checks+=1;identity=hashlib.sha256(f'{name}:{snapshot}'.encode()).hexdigest()[:32]
  if identity in targets:
   item={'workspace_run_id':name,'snapshot_id':snapshot,'initialization_id':parent,'initialization_control_path':Path(ledger).relative_to('/data').as_posix(),'parent':runinfo[(ledger,parent)]}
   if item not in out['matches'].setdefault(identity,[]):out['matches'][identity].append(item)
out.update(candidate_workspaces=len(names),attempts=len(attempts),checks=checks,elapsed=time.monotonic()-start,unmatched=sorted(targets-set(out['matches'])));print(json.dumps(out))
