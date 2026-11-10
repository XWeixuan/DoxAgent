"""Exact Projector-only overlay; preserve the accepted Runtime/Worker images."""
import argparse
import copy
from datetime import UTC,datetime
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
REPO=Path('/home/ubuntu/doxagent')
ROLE='v2-projector'
CONTAINER='doxagent-v2-v2-projector-1'
SOURCE='src/doxagent/v2_read/outbox.py'


def git(commit,path):
    return subprocess.check_output(['sudo','-u','ubuntu','git','-C',str(REPO),'show',commit+':'+path])


def inspect():
    return json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]


def actual_hash():
    raw=subprocess.check_output(['docker','cp',CONTAINER+':/app/'+SOURCE,'-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as ar:
        return hashlib.sha256(ar.extractfile(ar.getmembers()[0]).read()).hexdigest()


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','deploy'])
    parser.add_argument('--commit',required=True)
    parser.add_argument('--expected-source-hash',required=True)
    args=parser.parse_args()
    commit=subprocess.check_output(['sudo','-u','ubuntu','git','-C',str(REPO),'rev-parse',args.commit],text=True).strip()
    assert len(commit)==40
    audit=ROOT/('w3-cdc-'+commit[:12]);audit.mkdir(mode=0o700,exist_ok=True)
    owner=audit/'storage_release.py';owner.write_bytes(git(commit,'scripts/ops/storage_release.py'))
    spec=importlib.util.spec_from_file_location('release_owner',owner)
    release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
    with release.lock(ROOT):
        state=json.loads((ROOT/'state.json').read_bytes())
        info=inspect()
        assert info['Image']==state['components'][ROLE]['current']['image']
        assert actual_hash()==args.expected_source_hash,'unreviewed Projector source changed'
        if args.action=='build':
            context=audit/'build';target=context/SOURCE;target.parent.mkdir(parents=True,exist_ok=True)
            raw=git(commit,SOURCE);target.write_bytes(raw);expected=hashlib.sha256(raw).hexdigest()
            base='doxagent-v2:cdc-base-'+info['Image'][7:19]
            subprocess.run(['docker','tag',info['Image'],base],check=True)
            check="import hashlib;assert hashlib.sha256(open('/app/"+SOURCE+"','rb').read()).hexdigest()=="+repr(expected)
            recipe=['FROM '+base,'USER root','COPY --chmod=0644 '+SOURCE+' /app/'+SOURCE,
                    'RUN umask 022 && python -m compileall -q --invalidation-mode checked-hash /app/'+SOURCE,
                    'USER '+(info['Config'].get('User') or 'root'),'RUN python -c '+shlex.quote(check)]
            (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
            tag='doxagent-v2:w3-cdc-'+commit[:12]
            subprocess.run(['docker','build','-t',tag,str(context)],check=True)
            image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
            config=json.loads((ROOT/'compose.current.json').read_bytes())
            component=copy.deepcopy(config['services'][ROLE]);component['image']=image
            component['checks']=[['docker','exec',CONTAINER,'python','-c',check]]
            manifest=dict(commit=commit,compose_command=state['compose_base_command'],components={ROLE:component},baseline=info['Image'])
            if image not in state.setdefault('owned_images',[]):state['owned_images'].append(image)
            state.setdefault('candidates',[]).append(dict(image=image,expires=time.time()+48*3600))
            release.atomic(ROOT/'state.json',state)
            release.atomic(audit/'manifest.private.json',manifest)
            receipt=dict(at=datetime.now(UTC).isoformat(),commit=commit,role=ROLE,base=info['Image'],image=image,source_hash=expected)
            release.atomic(audit/'build.json',receipt)
        else:
            manifest=json.loads((audit/'manifest.private.json').read_bytes())
            assert set(manifest['components'])=={ROLE}
            assert info['Image']==manifest['baseline'],'another publication changed the baseline'
            for name in ['state.json','compose.current.json']:
                (audit/(name+'.before.private')).write_bytes((ROOT/name).read_bytes())
            guardian=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-initialization-guardian-1']))[0]
            for item in guardian['Config'].get('Env',[]):
                key,_,value=item.partition('=')
                if key.startswith('DOXAGENT_INITIALIZATION_REPAIR_'):os.environ[key]=value
            release.reconcile=lambda *_:dict(deferred='validate bounded receipt repair before retirement')
            subprocess.run(['docker','stop','-t','10',info['Id']],check=True)
            release.deploy(ROOT,state,manifest)
            receipt=dict(at=datetime.now(UTC).isoformat(),commit=commit,role=ROLE,image=inspect()['Image'])
            release.atomic(audit/'release.json',receipt)
        print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
