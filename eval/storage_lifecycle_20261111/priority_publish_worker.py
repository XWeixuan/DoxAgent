"""Operator explicitly authorized interrupting current models to publish now."""
from contextlib import closing
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

root=Path('/var/lib/doxagent/releases')
audit=root/'storage-lifecycle-priority-worker-20261111'
audit.mkdir(exist_ok=True,mode=0o700)
report=dict(started_at=datetime.now(UTC).isoformat(),pid=os.getpid(),authorization='interrupt current model Jobs and prioritize release',released=False,containers=[],resumed=[])
database='file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3?mode=ro'

def save():
    p=audit/'window.pending';p.write_text(json.dumps(report,indent=2));p.replace(audit/'window.json')

def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]

def jobs():
    with closing(sqlite3.connect(database,uri=True)) as db:
        return db.execute("SELECT id,status,json_extract(request,'$.node') FROM jobs WHERE status IN ('queued','running')").fetchall()

def interrupted(*_):raise InterruptedError('priority release interrupted; restore producers')

for signum in [signal.SIGTERM,signal.SIGHUP,signal.SIGINT]:signal.signal(signum,interrupted)
waiting=root/'storage-lifecycle-worker-only-window-20261111/window.json'
previous=json.loads(waiting.read_bytes())
if not previous.get('finished_at'):
    pid=3608905
    cmd=Path(f'/proc/{pid}/cmdline').read_bytes()
    if cmd.replace(b'\0',b' ').strip()!=b'python3 -':raise RuntimeError('waiting process identity changed')
    start=subprocess.check_output(['ps','-p',str(pid),'-o','lstart='],text=True).strip()
    if '06:34:00 2026' not in start:raise RuntimeError('waiting process start changed')
    os.kill(pid,signal.SIGTERM)
    deadline=time.monotonic()+60
    while not json.loads(waiting.read_bytes()).get('finished_at'):
        if time.monotonic()>deadline:raise TimeoutError('previous owner has not restored producers')
        time.sleep(1)
report['previous_owner_finished']=json.loads(waiting.read_bytes()).get('finished_at');save()
old_worker=inspect('doxagent-v2-codex-worker-1')['Id']
try:
    names=['doxagent-v2-v2-scheduler-1',*[v['name'] for v in previous['containers']]]
    for name in names:
        current=inspect(name)
        row=dict(name=name,id=current['Id'],was_running=current['State']['Running'])
        report['containers'].append(row);save()
        if row['was_running']:subprocess.run(['docker','stop','-t','15',row['id']],check=True,timeout=45)
    report['initial_jobs']=jobs();save()
    code='''import asyncio,json,os
import httpx
ids=IDS
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8791',timeout=120,headers={'Authorization':'Bearer '+os.environ['DOXAGENT_CODEX_WORKER_BEARER_TOKEN']}) as client:
  async def cancel(identity):
   response=await client.delete('/v1/jobs/'+identity);response.raise_for_status();v=response.json()
   return {'job_id':identity,'status':v['status'],'error_code':v.get('error_code')}
  print(json.dumps(await asyncio.gather(*(cancel(identity) for identity in ids))))
asyncio.run(main())
'''.replace('IDS',repr([v[0] for v in report['initial_jobs']]))
    result=subprocess.run(['docker','exec','-i','doxagent-v2-codex-worker-1','python','-'],input=code,text=True,capture_output=True,timeout=150)
    (audit/'cancel.stderr').write_text(result.stderr)
    if result.returncode:raise RuntimeError('authorized cancellation failed; see private cancel.stderr')
    report['cancellation_results']=json.loads(result.stdout);save()
    if jobs():raise RuntimeError('models still active after cancellation; Worker remains serving')
    result=subprocess.run(['python3',str(root/'storage_lifecycle_release_20261111.py'),'deploy','--commit','187d79f3'],capture_output=True,text=True,timeout=300)
    (audit/'deploy.stdout').write_text(result.stdout);(audit/'deploy.stderr').write_text(result.stderr)
    if result.returncode:raise RuntimeError('canonical release failed; see private deploy.stderr')
    report['released']=True
except BaseException as exc:
    report['error']=type(exc).__name__+':'+str(exc);raise
finally:
    current=inspect('doxagent-v2-codex-worker-1')
    if current['Id']==old_worker and not current['State']['Running']:
        subprocess.run(['docker','start',old_worker],check=True,timeout=45)
    report['worker_image']=inspect('doxagent-v2-codex-worker-1')['Image']
    for row in report['containers']:
        if not row['was_running']:continue
        current=inspect(row['name'])
        if current['Id']!=row['id']:
            report['resumed'].append(dict(name=row['name'],result='concurrent replacement preserved'));continue
        if not current['State']['Running']:subprocess.run(['docker','start',row['id']],check=True,timeout=45)
        report['resumed'].append(dict(name=row['name'],result='running'))
    report['finished_at']=datetime.now(UTC).isoformat();save();print(json.dumps(report),flush=True)
