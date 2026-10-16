"""Publish committed Worker first, then Scheduler and failure read projections."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime

repo = pathlib.Path('/home/ubuntu/doxagent')
root = pathlib.Path('/var/lib/doxagent/w3-turn-observation-20261017')
reference_fix = '--reference-fix' in sys.argv
if reference_fix:
    root = root/'reference-fix'
build = json.loads((root/'build.json').read_text())
git = ['git','-c','safe.directory='+str(repo)]
def run(args, timeout=60):
    return subprocess.check_output(args,cwd=repo,text=True,timeout=timeout).strip()
def inventory():
    ids=run(['docker','ps','-q','--filter','label=com.docker.compose.project=doxagent-v2']).split()
    return {c['Name'].lstrip('/'):{'id':c['Id'],'image':c['Config']['Image'],
                                 'image_id':c['Image']}
            for c in json.loads(run(['docker','inspect',*ids]))}
assert run(git+['rev-parse','HEAD']) == build['commit']
assert not run(git+['status','--porcelain','--untracked-files=no'])
assert shutil.disk_usage('/').free > 3*1024**3
before=inventory()
# Isolated immutable-image check: no network, auth or production volumes.
check="""
import sys, tempfile
from pathlib import Path
from doxagent.codex_worker.w3_turn_probe import classify,union_ms,read_control
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
assert 'doxagent.tools.factory' not in sys.modules
assert union_ms([(0,2),(1,4)])==4
assert classify('web_search','earliest original source')['business_labels']
with tempfile.TemporaryDirectory() as p: assert not read_control(Path(p))
print('immutable_image_probe_ok')
"""
print(run(['docker','run','--rm','--network','none','--entrypoint','python',build['image'],'-c',check],120),flush=True)
env=repo/'.env.v2'
backup=root/'env.before'
assert not backup.exists(), 'release_already_started'
shutil.copy2(env,backup)
os.chmod(backup,0o600)
stat=env.stat()
updates={'DOXAGENT_V2_IMAGE':build['image'],'DOXAGENT_BUILD_COMMIT':build['commit'],
         'DOXAGENT_SOURCE_SHA256':build['source_sha256']}
lines=env.read_text().splitlines()
for i,line in enumerate(lines):
    key=line.split('=',1)[0]
    if key in updates: lines[i]=key+'='+updates.pop(key)
lines.extend(k+'='+v for k,v in updates.items())
tmp=root/'env.next'
tmp.write_text('\n'.join(lines)+'\n')
os.chmod(tmp,stat.st_mode&0o777)
os.chown(tmp,stat.st_uid,stat.st_gid)
os.replace(tmp,env)
compose=['docker','compose','-p','doxagent-v2','--project-directory',str(repo),'--env-file',str(env)]
for name in ('docker-compose.v2-production.yml','deploy/docker-compose.server.yml',
             'deploy/docker-compose.initialization-repair.yml'):
    compose.extend(['-f',str(repo/name)])
run(compose+['config','--quiet'])
print('Gracefully stopping old Scheduler before Worker protocol replacement.',flush=True)
run(['docker','stop','--time','-1',before['doxagent-v2-v2-scheduler-1']['id']],600)
idle="""
import sqlite3,json
d=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True)
d.execute('PRAGMA query_only=ON')
print(d.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('running','queued')").fetchone()[0])
"""
for attempt in range(120):
    count=int(run(['docker','exec','doxagent-v2-codex-worker-1','python','-c',idle]))
    if count==0:break
    if attempt%6==0:print('Draining existing Worker jobs:',count,flush=True)
    time.sleep(5)
else:raise RuntimeError('Worker still active; do not interrupt model turns')
run(['docker','stop','--time','-1',before['doxagent-v2-codex-worker-1']['id']],120)
run(compose+['up','-d','--no-deps','--no-build','codex-worker'],180)
for _ in range(60):
    state=json.loads(run(['docker','inspect','doxagent-v2-codex-worker-1']))[0]['State']
    if state.get('Health',{}).get('Status')=='healthy':break
    time.sleep(2)
else:raise RuntimeError('new_worker_not_healthy')
print(run(['docker','exec','doxagent-v2-codex-worker-1','python','-m',
           'doxagent.codex_worker.w3_turn_probe',
           *(['status'] if reference_fix else ['enable','--hours','8','--max-samples','500'])]),flush=True)
run(compose+['up','-d','--no-deps','--no-build','v2-scheduler','v2-api','v2-projector'],180)
after=inventory()
changed={'doxagent-v2-codex-worker-1','doxagent-v2-v2-scheduler-1',
         'doxagent-v2-v2-api-1','doxagent-v2-v2-projector-1'}
assert all(after[k]['id']==v['id'] for k,v in before.items() if k not in changed)
assert all(after[k]['image']==build['image'] for k in changed)
receipt={**build,'at':datetime.now(UTC).isoformat(),'before':before,'after':after,
         'unrelated_containers_unchanged':True,'probe_hours':8,'max_samples':500,
         'historical_cases_resumed':0}
(root/'release.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps({k:v for k,v in receipt.items() if k not in ('before','after')}),flush=True)
