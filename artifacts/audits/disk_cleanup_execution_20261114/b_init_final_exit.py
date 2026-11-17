import os,json,time,sqlite3,hashlib,shutil
from pathlib import Path
from collections import defaultdict
from contextlib import closing
from datetime import datetime,timezone
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.initialization_owners import control_paths
from doxagent.codex_worker.snapshot_retention import protection
from doxagent.trade_execution.worker import WriterLock
from doxagent.v2_read.maintenance import atomic_json
from doxagent.codex_runtime.immutable_evidence import collect_unused
s=DoxAgentSettings();w=Path('/data/workspaces');now=datetime.now(timezone.utc);out={'at':now.isoformat(),'retired':[],'kept':[],'errors':[]};maps={};pins=set()
def jobs():
 with closing(sqlite3.connect((w/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:return [x[0] for x in db.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE status IN ('queued','running')") if x[0]]
with WriterLock(w/'initialization-snapshot-retirement'):
 for p in control_paths(s):
  with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
   runs,held,ps=protection(db,s.persistent_runtime_v2_sqlite_path,primary=p==Path(s.ticker_initialization_control_path).resolve());pins.update(ps);attempts={a:r for a,r in db.execute('SELECT execution_id,run_id FROM initialization_attempts')};maps[str(p)]=(runs,attempts)
 groups=defaultdict(list);live=jobs()
 for marker in (w/'.init-owners').glob('*.json'):
  v=json.loads(marker.read_bytes());target=w/'.init'/marker.stem
  if v.get('state')!='LIVE' or not target.is_dir() or target.is_symlink() or marker.stem in pins:continue
  run=v['workspace_run_id'];snapshot=v['snapshot_id'];matches=[]
  assert marker.stem==hashlib.sha256(f'{run}:{snapshot}'.encode()).hexdigest()[:32]
  for ledger,(runs,attempts) in maps.items():
   parent=attempts.get(snapshot) or v.get('initialization_id')
   if parent in runs:matches.append((ledger,parent))
  if v.get('initialization_control_path'):
   exact=[x for x in matches if Path(x[0]).relative_to('/data').as_posix()==v['initialization_control_path']]
   if exact:matches=exact
  if len(matches)!=1:continue
  ledger,parent=matches[0];owner=maps[ledger][0][parent]
  if owner['status']!='SUCCEEDED' or owner['manual'] or not owner['at'] or now-datetime.fromisoformat(owner['at'])<__import__('datetime').timedelta(hours=24):continue
  if not (w/run).is_dir() or any(j==parent or j.startswith(parent+'-') or j==run for j in live):continue
  with closing(sqlite3.connect(Path(ledger).as_uri()+'?mode=ro',uri=True)) as db:
   accepted=db.execute("SELECT 1 FROM initialization_nodes WHERE run_id=? AND node_key IN ('activation.commit','d3') AND json_extract(payload,'$.status')='SUCCEEDED'",(parent,)).fetchone()
  if not accepted:continue
  groups[(ledger,parent,run)].append((target.stat().st_mtime,marker,v,target))
 pending=[]
 for key,items in groups.items():
  items.sort(key=lambda x:x[0]);out['kept'].extend([])
  pending.extend((key,item) for item in items[:])
 print(json.dumps({'stage':'preflight','candidate_snapshots':len(pending),'kept_latest':len(out['kept'])}),flush=True)
 for (ledger,parent,run),(_,marker,v,target) in pending:
  try:
   with closing(sqlite3.connect(ledger,timeout=10)) as db,db:
    db.execute('BEGIN IMMEDIATE')
    status=db.execute("SELECT json_extract(payload,'$.status'),json_extract(payload,'$.manual_resume_required') FROM initialization_runs WHERE id=?",(parent,)).fetchone()
    if status[0]!='SUCCEEDED' or status[1] or any(j==parent or j.startswith(parent+'-') or j==run for j in jobs()):continue
    if db.execute("SELECT 1 FROM initialization_nodes n JOIN initialization_runs r ON r.id=n.run_id WHERE n.node_key='internal.rerun' AND json_extract(r.payload,'$.status') NOT IN ('SUCCEEDED','FAILED','CANCELLED') LIMIT 1").fetchone():continue
    retired=w/'.init-retired'/target.name;assert target.resolve().parent==(w/'.init').resolve() and not retired.exists()
    v.update(state='RETIRED',at=now.isoformat(),initialization_id=parent,initialization_control_path=Path(ledger).relative_to('/data').as_posix(),reason='completed-pre-node-history-final-artifacts-retained',resume_allowed=False)
    atomic_json(marker,v);retired.parent.mkdir(exist_ok=True);os.replace(target,retired)
   allocated=0;files=0
   for directory,dirs,names in os.walk(retired,followlinks=False):
    for name in names:
     p=Path(directory)/name;st=p.lstat();files+=1
     if st.st_nlink==1:allocated+=st.st_blocks*512
   shutil.rmtree(retired);out['retired'].append({'id':target.name,'workspace':run,'owner':parent,'single_link_allocated':allocated,'files':files})
   if len(out['retired'])%50==0:print(json.dumps({'stage':'retired','count':len(out['retired']),'single_link_allocated':sum(x['single_link_allocated'] for x in out['retired'])}),flush=True)
  except Exception as e:out['errors'].append({'snapshot':target.name,'error':str(e)})
 out['unused_content_files_removed']=collect_unused(w/'.init-contents',limit=100000)
print(json.dumps({'result':out}),flush=True)
