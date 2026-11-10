"""Exact display-role overlays; preserve accepted Runtime/Worker images."""
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


def inspect(container=CONTAINER):
    return json.loads(subprocess.check_output(['docker','inspect',container]))[0]


def actual_hash(container,path):
    raw=subprocess.check_output(['docker','cp',container+':/app/'+path,'-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as ar:
        return hashlib.sha256(ar.extractfile(ar.getmembers()[0]).read()).hexdigest()


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','deploy'])
    parser.add_argument('--commit',required=True)
    parser.add_argument('--expected-source-hash',required=True)
    parser.add_argument('--role',choices=[ROLE,'v2-api'],default=ROLE)
    parser.add_argument('--source-path',choices=[SOURCE,'src/doxagent/v2_read/serving_fields.py','src/doxagent/api_v2/case_detail.py'],default=SOURCE)
    args=parser.parse_args()
    source=args.source_path
    role=args.role;container='doxagent-v2-'+role+'-1'
    assert (role=='v2-api')==(source=='src/doxagent/api_v2/case_detail.py')
    commit=subprocess.check_output(['sudo','-u','ubuntu','git','-C',str(REPO),'rev-parse',args.commit],text=True).strip()
    assert len(commit)==40
    audit=ROOT/('w3-cdc-'+commit[:12]+('-'+role if role!=ROLE else ''));audit.mkdir(mode=0o700,exist_ok=True)
    owner=audit/'storage_release.py';owner.write_bytes(git(commit,'scripts/ops/storage_release.py'))
    spec=importlib.util.spec_from_file_location('release_owner',owner)
    release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
    with release.lock(ROOT):
        state=json.loads((ROOT/'state.json').read_bytes())
        info=inspect(container)
        assert info['Image']==state['components'][role]['current']['image']
        assert actual_hash(container,source)==args.expected_source_hash,'unreviewed display source changed'
        if args.action=='build':
            context=audit/'build';target=context/source;target.parent.mkdir(parents=True,exist_ok=True)
            raw=git(commit,source);target.write_bytes(raw);expected=hashlib.sha256(raw).hexdigest()
            base='doxagent-v2:cdc-base-'+info['Image'][7:19]
            subprocess.run(['docker','tag',info['Image'],base],check=True)
            check="import hashlib;assert hashlib.sha256(open('/app/"+source+"','rb').read()).hexdigest()=="+repr(expected)
            recipe=['FROM '+base,'USER root','COPY --chmod=0644 '+source+' /app/'+source,
                    'RUN umask 022 && python -m compileall -q --invalidation-mode checked-hash /app/'+source,
                    'USER '+(info['Config'].get('User') or 'root'),'RUN python -c '+shlex.quote(check)]
            (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
            tag='doxagent-v2:w3-cdc-'+commit[:12]+'-'+role
            subprocess.run(['docker','build','-t',tag,str(context)],check=True)
            image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
            config=json.loads((ROOT/'compose.current.json').read_bytes())
            component=copy.deepcopy(config['services'][role]);component['image']=image
            component['checks']=[['docker','exec',container,'python','-c',check]]
            manifest=dict(commit=commit,compose_command=state['compose_base_command'],components={role:component},baseline=info['Image'],source_path=source)
            if image not in state.setdefault('owned_images',[]):state['owned_images'].append(image)
            state.setdefault('candidates',[]).append(dict(image=image,expires=time.time()+48*3600))
            release.atomic(ROOT/'state.json',state)
            release.atomic(audit/'manifest.private.json',manifest)
            receipt=dict(at=datetime.now(UTC).isoformat(),commit=commit,role=role,base=info['Image'],image=image,source_hash=expected)
            release.atomic(audit/'build.json',receipt)
        else:
            manifest=json.loads((audit/'manifest.private.json').read_bytes())
            assert manifest['source_path']==source
            assert set(manifest['components'])=={role}
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
            receipt=dict(at=datetime.now(UTC).isoformat(),commit=commit,role=role,image=inspect(container)['Image'])
            release.atomic(audit/'release.json',receipt)
        print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
