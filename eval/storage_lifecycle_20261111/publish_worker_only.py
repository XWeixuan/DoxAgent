"""Finite release window; accepted models and realtime Scheduler keep running."""
from contextlib import closing
from datetime import UTC,datetime
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

commit='187d79f3'
root=Path('/var/lib/doxagent/releases')
audit=root/'storage-lifecycle-worker-only-window-20261111'
audit.mkdir(exist_ok=True,mode=0o700)
receipt=audit/'window.json'
report=dict(started_at=datetime.now(UTC).isoformat(),released=False,containers=[],resumed=[])
database='file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3?mode=ro'

def save():
    pending=receipt.with_suffix('.pending');pending.write_text(json.dumps(report,indent=2));pending.replace(receipt)

def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]

def active():
    with closing(sqlite3.connect(database,uri=True,timeout=.2)) as db:
        return db.execute("SELECT id,status,json_extract(request,'$.node') FROM jobs WHERE status IN ('queued','running')").fetchall()

def interrupted(*_):raise InterruptedError('release window interrupted; restore producers')

for signum in [signal.SIGTERM,signal.SIGHUP,signal.SIGINT]:signal.signal(signum,interrupted)
previous=json.loads((root/'w3-latency-7fe1ffc8b001/non-w3-pause.json').read_bytes())
old_worker=inspect('doxagent-v2-codex-worker-1')['Id']
try:
    # The previously enumerated six non-W3 producers are the entire pause
    # scope. Snapshot immutable identities before stopping; never stop Worker.
    for item in previous['containers']:
        info=inspect(item['name'])
        row=dict(name=item['name'],id=info['Id'],was_running=info['State']['Running'])
        report['containers'].append(row);save()
        if row['was_running']:
            subprocess.run(['docker','stop','-t','15',row['id']],check=True,timeout=45)
    report['initial_jobs']=active();save()
    if any(node!='persistent_runtime_w3' for _,_,node in report['initial_jobs']):
        report['next_background_check_seconds']=1800;save()
        time.sleep(1800)
    # Only the brief final realtime release window checks more often. Never
    # keep background production paused indefinitely to chase global idleness.
    deadline=time.monotonic()+300
    while True:
        jobs=active();report['last_jobs']=jobs;save()
        if any(node!='persistent_runtime_w3' for _,_,node in jobs):
            raise TimeoutError('background models still active after the half-hour window')
        if not jobs:
            attempt=subprocess.run(['python3',str(root/'storage_lifecycle_release_20261111.py'),
                'deploy','--commit',commit],capture_output=True,text=True)
            (audit/'deploy.stdout').write_text(attempt.stdout)
            (audit/'deploy.stderr').write_text(attempt.stderr)
            if attempt.returncode==0:
                report['released']=True;break
            if 'Worker has accepted work' not in attempt.stderr:
                raise RuntimeError('canonical release failed; inspect private deploy.stderr')
        if time.monotonic()>=deadline:raise TimeoutError('no idle model Job window; keep serving')
        time.sleep(30)
except BaseException as exc:
    report['error']=type(exc).__name__+':'+str(exc);raise
finally:
    # Restore only our unchanged container identities. Preserve concurrent
    # replacements rather than reviving an old role from its historical ID.
    worker=inspect('doxagent-v2-codex-worker-1')
    if worker['Id']==old_worker and not worker['State']['Running']:
        subprocess.run(['docker','start',old_worker],check=True,timeout=45)
    for row in report['containers']:
        if not row['was_running']:continue
        current=inspect(row['name'])
        if current['Id']!=row['id']:
            report['resumed'].append(dict(name=row['name'],result='concurrent replacement preserved'));continue
        if not current['State']['Running']:
            subprocess.run(['docker','start',row['id']],check=True,timeout=45)
        report['resumed'].append(dict(name=row['name'],result='running'))
    report['finished_at']=datetime.now(UTC).isoformat()
    ids=[row[0] for row in report.get('initial_jobs',[])]
    if ids:
        with closing(sqlite3.connect(database,uri=True,timeout=.2)) as db:
            report['initial_job_outcomes']=db.execute('SELECT id,status FROM jobs WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchall()
    save();print(json.dumps(report),flush=True)
