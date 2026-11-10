"""Exact-file builds over actual production images, using the existing release owner."""
import argparse
import copy
from datetime import UTC,datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT=Path('/var/lib/doxagent/releases')
REPO=Path('/home/ubuntu/doxagent')


def command(*args):return subprocess.check_output(args,text=True).strip()


def git(commit,path):
    return subprocess.check_output(['sudo','-u','ubuntu','git','-C',str(REPO),'show',commit+':'+path])


def inspect(role):return json.loads(command('docker','inspect','doxagent-v2-'+role+'-1'))[0]


def actual_file(role,path):
    result=subprocess.run(['docker','cp','doxagent-v2-'+role+'-1:/app/'+path,'-'],capture_output=True)
    if result.returncode:return None
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as ar:return ar.extractfile(ar.getmembers()[0]).read()


def load_release(audit,commit):
    path=audit/'storage_release.py';path.write_bytes(git(commit,'scripts/ops/storage_release.py'))
    spec=importlib.util.spec_from_file_location('storage_owner',path);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(audit,commit,baseline,release):
    conf=json.loads((ROOT/'compose.current.json').read_bytes())
    state=json.loads((ROOT/'state.json').read_bytes())
    infos={r:inspect(r) for r in baseline['roles']}
    groups={};hashes={};selected={}
    for role,proof in baseline['roles'].items():
        info=infos[role]
        if info['Image']!=proof['image']:raise ValueError('production image changed: '+role)
        selected[role]=list(proof['files'])
        for path,old in proof['files'].items():
            raw=actual_file(role,path)
            if (hashlib.sha256(raw).hexdigest() if raw else None)!=(old['sha256'] if old else None):
                raise ValueError('production source changed: '+role+' '+path)
        groups.setdefault(info['Image'],[]).append(role)
    labels=infos['v2-scheduler']['Config']['Labels']
    base=['docker','compose','--env-file',str(REPO/'.env.v2'),'-p',labels['com.docker.compose.project']]
    for path in labels['com.docker.compose.project.config_files'].split(','):
        if path!=str(ROOT/'compose.current.json'):base+=['-f',path]
    manifest={'compose_command':base,'commit':commit,'components':{},'baseline':{r:v['Image'] for r,v in infos.items()}}
    receipt={'commit':commit,'at':datetime.now(UTC).isoformat(),'images':{},'files':{}}
    for image,roles in groups.items():
        paths=sorted({p for role in roles for p in selected[role]})
        context=audit/('build-'+image[7:19]);context.mkdir(exist_ok=True)
        for path in paths:
            raw=git(commit,path);target=context/path;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(raw);hashes[path]=hashlib.sha256(raw).hexdigest()
        base_tag='doxagent-v2:storage-base-'+image[7:19]
        subprocess.run(['docker','tag',image,base_tag],check=True)
        lines=['FROM '+base_tag,'USER root']+['COPY --chmod=0644 '+p+' /app/'+p for p in paths]
        lines+=['RUN python -m compileall -q '+' '.join('/app/'+p for p in paths),
                'LABEL com.doxagent.storage-lifecycle.commit="'+commit+'"',
                'USER '+(infos[roles[0]]['Config'].get('User') or 'root')]
        smoke=';'.join('compile(open('+repr('/app/'+p)+').read(),'+repr(p)+',"exec")' for p in paths)
        lines+=['RUN python -c '+__import__('shlex').quote(smoke)]
        (context/'Dockerfile').write_text('\n'.join(lines)+'\n')
        tag='doxagent-v2:storage-'+commit[:12]+'-'+image[7:19]
        subprocess.run(['docker','build','-t',tag,str(context)],check=True)
        candidate=command('docker','image','inspect','--format','{{.Id}}',tag)
        if candidate not in state.setdefault('owned_images',[]):state['owned_images'].append(candidate)
        state.setdefault('candidates',[]).append({'image':candidate,'expires':__import__('time').time()+48*3600})
        for role in roles:
            item=copy.deepcopy(conf['services'].get(role,{}));item['image']=candidate
            item['schema']=state['components'][role]['current'].get('schema')
            item['health_seconds']=180
            expected={p:hashes[p] for p in selected[role]}
            code='import hashlib;'+''.join('assert hashlib.sha256(open('+repr('/app/'+p)+',"rb").read()).hexdigest()=='+repr(h)+';' for p,h in expected.items())
            item['checks']=[['docker','exec','doxagent-v2-'+role+'-1','python','-c',code]]
            manifest['components'][role]=item
            receipt['images'][role]={'base':image,'candidate':candidate}
            receipt['files'][role]=expected
        release.atomic(ROOT/'state.json',state)
    release.atomic(audit/'manifest.private.json',manifest)
    release.atomic(audit/'build.json',receipt)
    print(json.dumps({'built':receipt['images'],'commit':commit}),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['build','deploy','reconcile'])
    p.add_argument('--commit',required=True);p.add_argument('--baseline',type=Path)
    args=p.parse_args();os.umask(0o077)
    commit=command('sudo','-u','ubuntu','git','-C',str(REPO),'rev-parse',args.commit)
    if len(commit)!=40:raise ValueError('full source commit required')
    audit=ROOT/('storage-lifecycle-'+commit[:12]);audit.mkdir(exist_ok=True,mode=0o700)
    release=load_release(audit,commit)
    with release.lock(ROOT):
        if args.action=='build':
            build(audit,commit,json.loads(args.baseline.read_bytes()),release)
        elif args.action=='deploy':
            manifest=json.loads((audit/'manifest.private.json').read_bytes())
            guardian=inspect('initialization-guardian')
            for entry in guardian['Config'].get('Env',[]):
                key,_,value=entry.partition('=')
                if key.startswith('DOXAGENT_INITIALIZATION_REPAIR_'):os.environ[key]=value
            # Worker replacement must never cancel accepted model work.
            db=__import__('sqlite3').connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True)
            try:
                if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                    raise ValueError('Worker has accepted work; finish producer drain before deploy')
            finally:db.close()
            for role,image in manifest['baseline'].items():
                if inspect(role)['Image']!=image:raise ValueError('new production release detected: '+role)
            state=json.loads((ROOT/'state.json').read_bytes())
            for name in ['state.json','compose.current.json']:
                shutil.copy2(ROOT/name,audit/(name+'.before.private'))
            # Readers/Worker first, then all D3 writers; preserve live env/template overrides.
            order=['v2-transport-maintenance','codex-worker','v2-message-collector','v2-projector','v2-maintenance','v2-site-access',
                   'v2-o4','v2-cdecr-executor','v2-initialization','v2-scheduler']
            manifest['components']={r:manifest['components'][r] for r in order if r in manifest['components']}
            release.reconcile=lambda *_: {'deferred':'verify business and owner progress before cleanup'}
            # Activate the new input representation only when every writer/reader is installed.
            gate=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/research/.d3-native-inputs-disabled')
            gate.touch(mode=0o600)
            stat=gate.parent.stat();os.chown(gate,stat.st_uid,stat.st_gid)
            result=release.deploy(ROOT,state,manifest)
            subprocess.run(['docker','exec','doxagent-v2-v2-transport-maintenance-1','python','-c',
                "from doxagent.v2_read.outbox import SourceOutbox; print(SourceOutbox('/data/runtime/runtime.sqlite3','runtime').migrate(('runtime_tasks',)))"],check=True)
            gate.unlink()
            state.setdefault('storage_formats',{})['d3-native-inputs-v1']={
                'at':datetime.now(UTC).isoformat(),'commit':commit,
                'rollback':'drain D3 writers; python -m doxagent.workflows.codex_document3.state_v21 --inline-for-rollback /data/research/research.sqlite3; then restore prior images'}
            release.atomic(ROOT/'state.json',state)
            # Same host-owned timer; replace its implementation after a successful release.
            shutil.copy2(ROOT/'storage_release.py',audit/'storage_release.before.py')
            release.atomic(audit/'release.json',{'at':datetime.now(UTC).isoformat(),'commit':commit,'result':result})
            temporary=ROOT/'storage_release.py.pending';temporary.write_bytes(git(commit,'scripts/ops/storage_release.py'))
            os.replace(temporary,ROOT/'storage_release.py')
            print(json.dumps({'deployed':order,'commit':commit}),flush=True)
        else:
            state=json.loads((ROOT/'state.json').read_bytes())
            print(json.dumps(release.reconcile(ROOT,state)),flush=True)


if __name__=='__main__':main()
