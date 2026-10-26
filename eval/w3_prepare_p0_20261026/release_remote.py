"""Canonical Worker-only release after verifying the existing job queue is empty."""
import copy
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

repo = Path('/home/ubuntu/doxagent')
root = Path('/var/lib/doxagent/releases')
commit = sys.argv[1]
spec = importlib.util.spec_from_file_location('release', repo/'scripts/ops/storage_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
def inventory():
    return {c['Config']['Labels'].get('com.docker.compose.service'): c
        for c in release.inspect_containers()
        if c['Config']['Labels'].get('com.docker.compose.project') == 'doxagent-v2'}
def jobs():
    code = "import sqlite3,json;d=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True);print(json.dumps(d.execute(\"SELECT id,status FROM jobs WHERE status IN ('queued','running')\").fetchall()))"
    return json.loads(subprocess.check_output(['docker','exec','doxagent-v2-codex-worker-1','python','-c',code],text=True))
with release.lock(root):
    before = inventory()
    worker = before['codex-worker']
    env = dict(item.split('=',1) for item in worker['Config']['Env'])
    if env.get('DOXAGENT_BUILD_COMMIT') != '3d0ae94cd85edd06df698345b6fe9eb1ee4baa7a':
        raise RuntimeError('Worker release changed since incident inspection; recheck before release')
    audit = root/('w3-permit-p0-'+commit[:12])
    audit.mkdir(exist_ok=True)
    build = release.build(repo,commit,root,['backend'])
    release.atomic(audit/'build.json',build)
    print(json.dumps({'phase':'built','commit':commit,'image':build['images']['backend']['image']}),flush=True)
    deadline = time.monotonic()+150
    while jobs():
        if time.monotonic()>=deadline:
            raise RuntimeError('Worker queue did not drain; no active job was interrupted')
        time.sleep(5)
    state=json.loads((root/'state.json').read_text())
    current=json.loads((root/'compose.current.json').read_text())
    candidate=copy.deepcopy(current['services']['codex-worker'])
    candidate['image']=build['images']['backend']['tag']
    candidate.setdefault('environment',{})['DOXAGENT_BUILD_COMMIT']=commit
    candidate['health_seconds']=150
    manifest={'commit':commit,'compose_command':state['compose_base_command'],
              'components':{'codex-worker':candidate}}
    release.atomic(audit/'manifest.private.json',manifest)
    release.deploy(root,state,manifest)
    after=inventory()
    assert all(after[name]['Id']==c['Id'] for name,c in before.items() if name!='codex-worker')
    result=after['codex-worker']
    result_env=dict(item.split('=',1) for item in result['Config']['Env'])
    assert result_env.get('DOXAGENT_BUILD_COMMIT')==commit and result['State']['Running']
    receipt={'commit':commit,'container':result['Id'],'image':result['Image'],
        'health':result['State'].get('Health',{}).get('Status'),
        'unrelated_container_ids_unchanged':True,'empty_job_queue_before_release':True,
        'audit_directory':str(audit)}
    release.atomic(audit/'receipt.json',receipt)
    print(json.dumps(receipt),flush=True)
