"""Projector-only overlay using the existing canonical release/rollback owner."""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import time

ROOT=Path('/var/lib/doxagent/releases')
REPO='/home/ubuntu/doxagent'
CONTAINER='doxagent-v2-v2-projector-1'
FILES=['src/doxagent/v2_read/'+n for n in
       ('projector.py','cli.py','outbox.py','native_content.py','executions.py')]

def git(commit,path):
    return subprocess.check_output(['sudo','-u','ubuntu','git','-C',REPO,'show',commit+':'+path])

def actual(path):
    raw=subprocess.check_output(['docker','cp',CONTAINER+':/app/'+path,'-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return archive.extractfile(archive.getmembers()[0]).read()

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','deploy'])
    parser.add_argument('--commit',required=True)
    args=parser.parse_args()
    commit=subprocess.check_output(['sudo','-u','ubuntu','git','-C',REPO,'rev-parse',args.commit],text=True).strip()
    directory=ROOT/('projection-performance-'+commit[:12]);directory.mkdir(exist_ok=True)
    owner=directory/'storage_release.py';owner.write_bytes(git(commit,'scripts/ops/storage_release.py'))
    spec=importlib.util.spec_from_file_location('release',owner);release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
    with release.lock(ROOT):
        state=json.loads((ROOT/'state.json').read_bytes())
        info=json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]
        assert info['Image']==state['components']['v2-projector']['current']['image']
        if args.action=='build':
            for path in FILES:
                assert actual(path)==git(commit+'^',path), 'live source changed: '+path
            context=directory/'build';context.mkdir(exist_ok=True)
            hashes={}
            for path in FILES:
                raw=git(commit,path);target=context/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
                hashes[path]=hashlib.sha256(raw).hexdigest()
            check='import hashlib; expected='+repr(hashes)+'; assert all(hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest()==h for p,h in expected.items())'
            recipe=['FROM '+info['Image'],'USER root']
            recipe += ['COPY --chmod=0644 '+p+' /app/'+p for p in FILES]
            recipe += ['RUN umask 022 && python -m compileall -q --invalidation-mode checked-hash /app/src/doxagent/v2_read',
                       'USER '+(info['Config'].get('User') or 'root'),'RUN python -c '+shlex.quote(check)]
            (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
            tag='doxagent-v2:projection-performance-'+commit[:12]
            subprocess.run(['docker','build','-t',tag,str(context)],check=True)
            image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
            config=json.loads((ROOT/'compose.current.json').read_bytes())
            candidate=copy.deepcopy(config['services']['v2-projector']);candidate['image']=image
            candidate['checks']=[['docker','exec',CONTAINER,'python','-c',check]]
            manifest=dict(commit=commit,compose_command=state['compose_base_command'],components={'v2-projector':candidate},baseline=info['Image'],hashes=hashes)
            state.setdefault('owned_images',[]).append(image)
            state.setdefault('candidates',[]).append(dict(image=image,expires=time.time()+48*3600))
            release.atomic(ROOT/'state.json',state);release.atomic(directory/'manifest.private.json',manifest)
            receipt=dict(commit=commit,baseline=info['Image'],image=image,hashes=hashes)
        else:
            manifest=json.loads((directory/'manifest.private.json').read_bytes())
            assert info['Image']==manifest['baseline'], 'another release changed the baseline'
            for path in FILES:assert actual(path)==git(commit+'^',path), 'source changed after build'
            for name in ('state.json','compose.current.json'):
                (directory/(name+'.before.private')).write_bytes((ROOT/name).read_bytes())
            release.reconcile=lambda *_: {'deferred':'normal host timer owns unrelated resource retirement'}
            subprocess.run(['docker','stop','-t','10',CONTAINER],check=True)
            release.deploy(ROOT,state,manifest)
            receipt=dict(commit=commit,image=json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]['Image'])
        release.atomic(directory/(args.action+'.json'),receipt)
        print(json.dumps(receipt),flush=True)

if __name__=='__main__':main()
