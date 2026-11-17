import importlib.util,sqlite3,json,subprocess,time
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');root=Path('/home/ubuntu/doxagent')
s=importlib.util.spec_from_file_location('idle',root/'scripts/ops/w3_latency_release_20261111.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def active():
 with sqlite3.connect((b/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
  return [(i,json.loads(p).get('run_id')) for i,p in d.execute("select id,payload from jobs where status in ('running','queued')")]
for i in range(120):
 rows=active();nonmaintenance=[j for j in rows if not str(j[1]).startswith('runtime-maintain-')]
 if not nonmaintenance:
  try:m.assert_scheduler_idle(b/'runtime/runtime.sqlite3');break
  except ValueError:pass
 if i%6==0:print(json.dumps(dict(waiting=True,active=len(rows),preserved_nonmaintenance=len(nonmaintenance))),flush=True)
 time.sleep(5)
else:raise SystemExit('No safe W3 idle window; services preserved')
print('W3 idle: stopping Scheduler for cutover',flush=True)
subprocess.run(['docker','stop','--time','45','doxagent-v2-v2-scheduler-1'],check=True)
try:
 rows=active();assert all(str(j[1]).startswith('runtime-maintain-') for j in rows), 'Preserve raced W3; abort cutover'
 subprocess.run(['docker','exec','-i','doxagent-v2-codex-worker-1','python','-'],input=Path('/home/ubuntu/w3-emergency-cancel-maintenance.py').read_bytes(),check=True)
 subprocess.run(['python3','/home/ubuntu/w3-nonsdk-release-20261117.py','deploy'],check=True)
finally:
 subprocess.run(['docker','start','doxagent-v2-v2-scheduler-1'],check=True)
 subprocess.run(['python3','/home/ubuntu/w3-nonsdk-hold.py','restore'],check=True)
