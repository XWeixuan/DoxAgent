import json,sqlite3,hashlib
from pathlib import Path
from collections import Counter
from contextlib import closing
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.initialization_owners import control_paths
from doxagent.codex_worker.snapshot_retention import protection
s=DoxAgentSettings();base=Path(s.codex_workspace_root) if hasattr(s,'codex_workspace_root') else Path('/data/workspaces');data=Path('/data');out={'ledgers':{},'owners':[],'active_jobs':[]};maps={};held=set();pins=set()
for p in control_paths(s):
 with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
  runs,h,pi=protection(db,s.persistent_runtime_v2_sqlite_path,primary=p==Path(s.ticker_initialization_control_path).resolve());held.update(h);pins.update(pi);attempts={a:r for a,r in db.execute('SELECT execution_id,run_id FROM initialization_attempts')}
  maps[str(p)]=(runs,attempts);out['ledgers'][str(p)]={'runs':runs,'held':sorted(h),'snapshot_pins':sorted(pi),'active':db.execute('SELECT * FROM ticker_active_revision').fetchall()}
with closing(sqlite3.connect((base/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:out['active_jobs']=db.execute("SELECT id,status,json_extract(payload,'$.run_id'),json_extract(request,'$.node') FROM jobs WHERE status IN ('queued','running')").fetchall()
for p in (base/'.init-owners').glob('*.json'):
 v=json.loads(p.read_bytes());run=v['workspace_run_id'];snapshot=v['snapshot_id'];matches=[]
 for ledger,(runs,attempts) in maps.items():
  parent=attempts.get(snapshot) or v.get('initialization_id')
  if parent in runs:matches.append((ledger,parent))
 if not matches:
  matches=[(ledger,parent) for ledger,(runs,_) in maps.items() for parent in runs if run==parent or run.startswith(parent+'-')]
  if matches:
   longest=max(len(x[1]) for x in matches);matches=[x for x in matches if len(x[1])==longest]
 if v.get('initialization_control_path'):
  exact=[x for x in matches if Path(x[0]).relative_to(data).as_posix()==v['initialization_control_path']]
  if exact:matches=exact
 out['owners'].append({'id':p.stem,'owner':v,'matches':matches,'held':any(parent in held for _,parent in matches),'pinned':p.stem in pins,'exists':(base/'.init'/p.stem).is_dir()})
print(json.dumps(out))
