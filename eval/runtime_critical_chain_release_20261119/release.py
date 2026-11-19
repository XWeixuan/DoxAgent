import importlib.util,json,os,subprocess,sys
from pathlib import Path
from datetime import datetime,UTC
os.umask(0o077)
repo=Path('/home/ubuntu/doxagent'); root=Path('/var/lib/doxagent/releases')
commit=subprocess.check_output(['git','rev-parse',sys.argv[1]],cwd=repo,text=True).strip()
roles=sys.argv[2:]
spec=importlib.util.spec_from_file_location('release_owner',repo/'scripts/ops/storage_release.py')
owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
built=json.loads((root/(commit+'.build.json')).read_text())
audit=root/'runtime-critical-chain-20261119';audit.mkdir(exist_ok=True)
with owner.lock(root):
    if 'v2-scheduler' in roles:
        subprocess.run(['docker','stop','--time','1200','doxagent-v2-v2-scheduler-1'],check=True)
    state=json.loads((root/'state.json').read_text())
    owner.build=lambda *a,**k:built
    try:
        receipt=owner.release_components(root,state,repo,commit,roles)
        owner.atomic(audit/(commit[:12]+'-'+'-'.join(roles)+'.json'),dict(at=datetime.now(UTC).isoformat(),commit=commit,roles=roles,receipt=receipt))
        print(json.dumps(dict(released=commit,roles=roles)),flush=True)
    finally:
        if 'v2-scheduler' in roles:
            subprocess.run(['docker','start','doxagent-v2-v2-scheduler-1'],check=False,stdout=subprocess.DEVNULL)
