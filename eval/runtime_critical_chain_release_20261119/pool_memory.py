import sqlite3,json
from pathlib import Path
from datetime import datetime,UTC
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces')
with sqlite3.connect((root/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
 rows=d.execute("SELECT id,capsule_id FROM w3_ready_slots WHERE state='READY' AND case_id IS NULL LIMIT 8").fetchall()
out={'at':datetime.now(UTC).isoformat(),'slots':[]}
def read(p):
 try:return Path(p).read_text()
 except OSError:return ''
def children(pid):
 result=[pid]
 tasks=Path(f'/proc/{pid}/task')
 try:threads=list(tasks.iterdir())[:128]
 except OSError:threads=[]
 descendants={child for thread in threads for child in read(thread/'children').split()}
 for child in descendants:
  if len(result)<32:result.extend(children(int(child)))
 return result
for identity,capsule in rows:
 try:owner=json.loads((root/'capsules'/capsule/'owner.json').read_text())
 except (OSError,ValueError):continue
 # Capsule PIDs are container namespace PIDs; resolve host PID by NSpid under Worker cgroup.
 import subprocess
 c=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-codex-worker-1']))[0]
 cp=c['State']['Pid'];cg=next(line.split(':',2)[2] for line in read(f'/proc/{cp}/cgroup').splitlines() if line.startswith('0::'))
 pids=read(Path('/sys/fs/cgroup')/cg.lstrip('/')/'cgroup.procs').split()
 host=None
 for p in pids:
  v=next((x.split()[1:] for x in read(f'/proc/{p}/status').splitlines() if x.startswith('NSpid:')),[])
  if v and int(v[-1])==owner['pid']:host=int(p);break
 total=0;count=0
 for pid in children(host) if host else []:
  for line in read(f'/proc/{pid}/smaps_rollup').splitlines():
   if line.startswith('Pss:'):total+=int(line.split()[1]);count+=1
 with sqlite3.connect((root/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as check:
  if not check.execute("SELECT 1 FROM w3_ready_slots WHERE id=? AND state='READY' AND case_id IS NULL",(identity,)).fetchone():continue
 out['slots'].append({'slot':identity,'host_pid':host,'processes':count,'pss_kib':total})
print(json.dumps(out))
