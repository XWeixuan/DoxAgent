"""Bounded production verification; read-only, no owner constructors or model calls."""
from contextlib import closing
from datetime import UTC,datetime
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import sqlite3
import subprocess
import tarfile
import time

root=Path('/var/lib/doxagent/releases')
base=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out={'at':datetime.now(UTC).isoformat()}
for key,name in [('priority','storage-lifecycle-priority-worker-20261111'),('previous_window','storage-lifecycle-worker-only-window-20261111')]:
    out[key]=json.loads((root/name/'window.json').read_bytes())
assert out['priority']['released']
names=list(dict.fromkeys([row['name'] for row in out['priority']['containers']]+[
    'doxagent-v2-'+role+'-1' for role in ['codex-worker','v2-maintenance','v2-transport-maintenance','v2-projector','v2-message-collector','v2-site-access']]))
out['containers']={}
for v in json.loads(subprocess.check_output(['docker','inspect',*names])):
    out['containers'][v['Name'].lstrip('/')]=dict(image=v['Image'],id=v['Id'],running=v['State']['Running'],health=v['State'].get('Health',{}).get('Status'),started_at=v['State']['StartedAt'],exit_code=v['State']['ExitCode'],finished_at=v['State']['FinishedAt'])
out['long_running_roles_ok']=all(v['running'] for name,v in out['containers'].items() if name not in {'doxagent-ionq-refresh-20261110','doxagent-cohr-refresh-20261110'})
out['one_shot_refreshes_ok']=all(v['running'] or v['exit_code']==0 for name,v in out['containers'].items() if name in {'doxagent-ionq-refresh-20261110','doxagent-cohr-refresh-20261110'})
build=json.loads((root/'storage-lifecycle-187d79f3896d/build.json').read_bytes())
out['source_files']={}
for path,expected in build['files']['codex-worker'].items():
    raw=subprocess.check_output(['docker','cp','doxagent-v2-codex-worker-1:/app/'+path,'-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as ar:raw=ar.extractfile(ar.getmembers()[0]).read()
    actual=hashlib.sha256(raw).hexdigest()
    out['source_files'][path]={'expected':expected,'actual':actual,'matched':expected==actual}
with closing(sqlite3.connect((base/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
    started=time.monotonic();db.set_progress_handler(lambda:time.monotonic()-started>3,1000)
    row=db.execute("SELECT value FROM metadata WHERE key='resource_retention'").fetchone()
    out['worker_owner']=json.loads(row[0]) if row else None
    out['active_jobs']=db.execute("SELECT status,json_extract(request,'$.node'),count(*) FROM jobs WHERE status IN ('queued','running') GROUP BY status,json_extract(request,'$.node')").fetchall()
    at=out['priority']['started_at']
    out['cancelled_during_priority']=db.execute("SELECT id,json_extract(request,'$.node') FROM jobs WHERE status='cancelled' AND json_extract(payload,'$.finished_at')>=?",(at,)).fetchall()
out['initialization_runs']={}
for path in sorted((base/'ops').glob('*-refresh-20261110/control.sqlite3')):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        out['initialization_runs'][path.relative_to(base).as_posix()]=db.execute("SELECT id,json_extract(payload,'$.status'),json_extract(payload,'$.manual_resume_required') FROM initialization_runs").fetchall()
target=PurePosixPath(json.loads((base/'read/active.json').read_bytes())['path'])
read_path=base.joinpath(*target.relative_to('/data').parts) if target.is_absolute() else base/'read'/str(target)
assert read_path.resolve().is_relative_to((base/'read').resolve())
receipt=Path(str(read_path)+'.gc.json')
out['read']={'path':str(read_path),'gc':json.loads(receipt.read_bytes()) if receipt.is_file() else None}
with closing(sqlite3.connect((base/'bus/acquisition.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
    started=time.monotonic();db.set_progress_handler(lambda:time.monotonic()-started>3,1000)
    row=db.execute("SELECT value FROM meta WHERE key='retention'").fetchone()
    out['acquisition']=json.loads(row[0]) if row else None
out['disk']=subprocess.check_output(['df','-B1','/'],text=True)
out['release_timer']=subprocess.check_output(['systemctl','is-active','doxagent-release-resources.timer'],text=True).strip()
out['host_code_sha256']=hashlib.sha256((root/'storage_release.py').read_bytes()).hexdigest()
print(json.dumps(out))
