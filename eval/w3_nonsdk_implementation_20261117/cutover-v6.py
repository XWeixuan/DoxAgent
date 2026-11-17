import importlib.util,sqlite3,json,subprocess,time,gzip
from datetime import datetime,UTC
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');root=Path('/home/ubuntu/doxagent')
s=importlib.util.spec_from_file_location('idle',root/'scripts/ops/w3_latency_release_20261111.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
for i in range(120):
 try:
  m.assert_scheduler_idle(b/'runtime/runtime.sqlite3')
  with sqlite3.connect((b/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
   active=d.execute("SELECT 1 FROM runtime_tasks WHERE kind='CASE' AND status='RUNNING' AND lease_until>? LIMIT 1",(datetime.now(UTC).isoformat(),)).fetchone()
  if not active:break
 except (ValueError,sqlite3.OperationalError):pass
 if i%6==0:print(json.dumps(dict(at=datetime.now(UTC).isoformat(),waiting_for_existing_cases=True)),flush=True)
 time.sleep(5)
else:raise SystemExit('No safe idle window; preserve running Scheduler')
raw=subprocess.check_output(['python3','/home/ubuntu/w3-nonsdk-postlogs.py'])
Path('/home/ubuntu/w3-nonsdk-prev6-logs.json.gz').write_bytes(gzip.compress(raw))
print('No active W3 owner or hot Case: cutting over Scheduler only',flush=True)
subprocess.run(['docker','stop','--timeout','5','doxagent-v2-v2-scheduler-1'],check=True)
try:subprocess.run(['python3','/home/ubuntu/w3-nonsdk-v6-release.py','deploy'],check=True)
finally:subprocess.run(['docker','start','doxagent-v2-v2-scheduler-1'],check=True)
