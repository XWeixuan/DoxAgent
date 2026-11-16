"""Use the existing release owner, with W3/Worker idle gates and bounded receipts."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import hashlib

repo=Path('/home/ubuntu/doxagent')
root=Path('/var/lib/doxagent/releases')
commit='27d8c0ab7e60949d73073374faa9081167ffdbd8'
audit=root/'w3-main-20261116'
audit.mkdir(mode=0o700,exist_ok=True)
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value
release=module('release_owner',repo/'scripts/ops/storage_release.py')
latency=module('idle_guard',repo/'scripts/ops/w3_latency_release_20261111.py')
def idle():
    data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
    with sqlite3.connect((data/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as db:
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError('Worker has accepted work; keep serving')
    latency.assert_scheduler_idle(data/'runtime/runtime.sqlite3')
if sys.argv[1]=='audit':
    changed=set(subprocess.check_output(['git','-C',str(repo),'diff','--name-only','0d2b7b5','HEAD']).decode().splitlines())
    code="import pathlib,json,hashlib;print(json.dumps({str(p.relative_to(pathlib.Path('/app'))):hashlib.sha256(p.read_bytes()).hexdigest() for package in ('persistent_runtime_v2','codex_worker','v2_read','api_v2') for p in (pathlib.Path('/app/src/doxagent')/package).rglob('*.py')}))"
    out={}
    for role in ('codex-worker','v2-scheduler','v2-projector','v2-api'):
        data=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-'+role+'-1','python','-c',code]))
        out[role]=[n for n,h in data.items() if (repo/n).exists() and hashlib.sha256((repo/n).read_bytes()).hexdigest()!=h and n not in changed]
    (audit/'source-audit.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out))
elif sys.argv[1]=='build':
    with release.lock(root):
        built=release.build(repo,commit,root,['backend','web'])
        (audit/'build.json').write_text(json.dumps(built,indent=2))
        print(json.dumps({k:v['image'] for k,v in built['images'].items()}))
elif sys.argv[1]=='deploy':
    with release.lock(root):
        idle()
        built=json.loads((audit/'build.json').read_text())
        assert built['commit']==commit
        for value in built['images'].values():
            assert json.loads(subprocess.check_output(['docker','image','inspect',value['image']]))[0]['Id']==value['image']
        state=json.loads((root/'state.json').read_text())
        # Same owner/Compose/health/rollback/lifecycle flow; reuse the exact
        # already built candidate instead of repeating the build at cutover.
        release.build=lambda *args,**kwargs:built
        components=['codex-worker','v2-api','v2-projector','v2-scheduler','v2-maintenance','v2-web',
                    'v2-initialization','v2-cdecr-executor','v2-o4','v2-executor','v2-delivery',
                    'v2-transport-maintenance','v2-control','v2-content-enrichment','v2-message-bus','v2-message-collector']
        result=release.release_components(root,state,repo,commit,components)
        (audit/'deploy.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
