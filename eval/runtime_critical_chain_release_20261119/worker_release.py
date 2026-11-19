"""One release window: drain accepted work, fenced idle cutover, restore owners."""
import importlib.util,json,os,sqlite3,subprocess,sys,time
from pathlib import Path
from datetime import datetime,UTC
os.umask(0o077)
repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
commit=subprocess.check_output(['git','rev-parse',sys.argv[1]],cwd=repo,text=True).strip()
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
owner=load('owner',repo/'scripts/ops/storage_release.py')
window=load('window',repo/'scripts/ops/storage_lifecycle_release_20261111.py')
built=json.loads((root/(commit+'.build.json')).read_text())
def note(stage,**kw):print(json.dumps(dict(at=datetime.now(UTC).isoformat(),stage=stage,**kw)),flush=True)
roles=['v2-maintenance','v2-initialization','v2-cdecr-executor','v2-o4','v2-scheduler']
stopped=[]
with owner.lock(root):
    try:
        for role in roles:
            identity='doxagent-v2-'+role+'-1'
            running=subprocess.check_output(['docker','inspect','--format','{{.State.Running}}',identity],text=True).strip()=='true'
            if running:
                stopped.append(identity)
                note('drain_owner',role=role)
                subprocess.run(['docker','stop','--timeout','1200',identity],check=True)
        deadline=time.monotonic()+900
        while True:
            with sqlite3.connect((data/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
                jobs=db.execute("SELECT id,status FROM jobs WHERE status IN ('queued','running') LIMIT 64").fetchall()
            if not jobs:break
            note('accepted_jobs_draining',jobs=jobs)
            if time.monotonic()>deadline:raise RuntimeError('keep Worker: accepted Jobs remain')
            time.sleep(5)
        window.stop_idle_worker(data/'workspaces/worker-jobs.sqlite3',data/'runtime/runtime.sqlite3','doxagent-v2-codex-worker-1')
        note('old_worker_stopped_idle')
        owner.build=lambda *a,**kw:built
        deploy=owner.deploy
        def configured(root,state,manifest):
            for role in ('codex-worker','v2-scheduler'):
                if role in manifest['components']:
                    manifest['components'][role].setdefault('environment',{})['DOXAGENT_W3_READY_LUNA']='6'
            return deploy(root,state,manifest)
        owner.deploy=configured
        state=json.loads((root/'state.json').read_text())
        receipt=owner.release_components(root,state,repo,commit,['codex-worker','v2-transport-maintenance','v2-maintenance','v2-initialization','v2-cdecr-executor','v2-scheduler'])
        audit=root/'runtime-critical-chain-20261119';audit.mkdir(exist_ok=True)
        owner.atomic(audit/'final-worker-release.json',dict(at=datetime.now(UTC).isoformat(),commit=commit,receipt=receipt))
        note('released',commit=commit)
    finally:
        subprocess.run(['docker','start','doxagent-v2-codex-worker-1'],check=False,stdout=subprocess.DEVNULL)
        for identity in reversed(stopped):
            subprocess.run(['docker','start',identity],check=False,stdout=subprocess.DEVNULL)
        note('owners_restored')
