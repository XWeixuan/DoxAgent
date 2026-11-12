"""Scoped projection overlays using the existing canonical release/rollback owner."""
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
       ('projector.py','repository.py','libraries.py','reference.py','formal.py','artifacts.py','lifecycle.py','projectors.py','bus_aggregates.py','outbox.py')]

def git(commit,path):
    return subprocess.check_output(['sudo','-u','ubuntu','git','-C',REPO,'show',commit+':'+path])

def actual(path):
    result=subprocess.run(['docker','cp',CONTAINER+':/app/'+path,'-'],capture_output=True)
    if result.returncode:
        if b'Could not find' in result.stderr:return None
        raise RuntimeError('cannot verify deployed file: '+path)
    raw=result.stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return archive.extractfile(archive.getmembers()[0]).read()

def main():
    global CONTAINER, FILES
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','deploy'])
    parser.add_argument('--commit',required=True)
    parser.add_argument('--baseline-commit')
    parser.add_argument('--role',choices=['v2-projector','v2-api','v2-web'],default='v2-projector')
    parser.add_argument('--web-archive',type=Path)
    parser.add_argument('--baseline-image')
    args=parser.parse_args()
    CONTAINER='doxagent-v2-'+args.role+'-1'
    if args.role=='v2-api':
        FILES=['src/doxagent/api_v2/'+n for n in ('app.py','views.py','projection_health.py','wire_schema.json')]
    if args.role=='v2-web':FILES=[]
    commit=subprocess.check_output(['sudo','-u','ubuntu','git','-C',REPO,'rev-parse',args.commit],text=True).strip()
    directory=ROOT/('projection-performance-'+commit[:12]+'-'+args.role);directory.mkdir(exist_ok=True)
    owner=directory/'storage_release.py';owner.write_bytes(git(commit,'scripts/ops/storage_release.py'))
    spec=importlib.util.spec_from_file_location('release',owner);release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
    with release.lock(ROOT):
        state=json.loads((ROOT/'state.json').read_bytes())
        info=json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]
        assert info['Image']==state['components'][args.role]['current']['image']
        if args.baseline_image:assert info['Image']==args.baseline_image
        baseline_commit=args.baseline_commit or commit+'^'
        if args.action=='build':
            for path in FILES:
                exists=subprocess.run(['sudo','-u','ubuntu','git','-C',REPO,'cat-file','-e',baseline_commit+':'+path],capture_output=True).returncode==0
                expected=git(baseline_commit,path) if exists else None
                assert actual(path)==expected, 'live source changed: '+path
            context=directory/'build';context.mkdir(exist_ok=True)
            hashes={}
            for path in FILES:
                raw=git(commit,path);target=context/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
                hashes[path]=hashlib.sha256(raw).hexdigest()
            check='import hashlib; expected='+repr(hashes)+'; assert all(hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest()==h for p,h in expected.items())'
            base='doxagent-v2:projection-base-'+info['Image'][7:19]
            subprocess.run(['docker','tag',info['Image'],base],check=True)
            recipe=['FROM '+base,'USER root']
            recipe += ['COPY --chmod=0644 '+p+' /app/'+p for p in FILES]
            if args.role=='v2-web':
                assert args.web_archive and args.web_archive.is_file()
                with tarfile.open(args.web_archive) as archive:
                    for item in archive.getmembers():
                        assert not item.issym() and not item.islnk() and (context/'web'/item.name).resolve().is_relative_to((context/'web').resolve())
                    archive.extractall(context/'web')
                sums=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(context/'web'))+'\n' for p in sorted((context/'web').rglob('*')) if p.is_file())
                (context/'web.sha256').write_text(sums)
                # extractall inherits the private release umask (077). Nginx
                # workers must still traverse directories and read public assets.
                recipe+=['COPY web/ /usr/share/nginx/html/',
                         'RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + && find /usr/share/nginx/html -type f -exec chmod 644 {} +',
                         'COPY web.sha256 /tmp/projection-web.sha256','RUN cd /usr/share/nginx/html && sha256sum -c /tmp/projection-web.sha256']
                web_check = '''set -eu
cd /usr/share/nginx/html
sha256sum -c /tmp/projection-web.sha256
html=$(wget -q -T 2 -O - http://127.0.0.1/index.html)
assets=$(printf '%s' "$html" | grep -oE '(src|href)="/assets/[^"]+"' | cut -d '"' -f 2)
test -n "$assets"
for asset in $assets; do wget -q -T 2 --spider "http://127.0.0.1$asset"; done
'''
                check_command=['docker','exec',CONTAINER,'sh','-c',web_check]
            else:
                recipe += ['RUN umask 022 && python -m compileall -q --invalidation-mode checked-hash '+(' /app/src/doxagent/api_v2' if args.role=='v2-api' else '/app/src/doxagent/v2_read'),
                       'USER '+(info['Config'].get('User') or 'root'),'RUN python -c '+shlex.quote(check)]
                check_command=['docker','exec',CONTAINER,'python','-c',check]
            (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
            tag='doxagent-v2:projection-performance-'+commit[:12]+'-'+args.role
            subprocess.run(['docker','build','-t',tag,str(context)],check=True)
            image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
            config=json.loads((ROOT/'compose.current.json').read_bytes())
            candidate=copy.deepcopy(config['services'][args.role]);candidate['image']=image
            candidate['checks']=[check_command]
            manifest=dict(commit=commit,compose_command=state['compose_base_command'],components={args.role:candidate},baseline=info['Image'],baseline_commit=baseline_commit,hashes=hashes)
            state.setdefault('owned_images',[]).append(image)
            state.setdefault('candidates',[]).append(dict(image=image,expires=time.time()+48*3600))
            release.atomic(ROOT/'state.json',state);release.atomic(directory/'manifest.private.json',manifest)
            receipt=dict(commit=commit,baseline=info['Image'],image=image,hashes=hashes)
        else:
            manifest=json.loads((directory/'manifest.private.json').read_bytes())
            assert info['Image']==manifest['baseline'], 'another release changed the baseline'
            for path in FILES:
                exists=subprocess.run(['sudo','-u','ubuntu','git','-C',REPO,'cat-file','-e',manifest['baseline_commit']+':'+path],capture_output=True).returncode==0
                assert actual(path)==(git(manifest['baseline_commit'],path) if exists else None), 'source changed after build'
            for name in ('state.json','compose.current.json'):
                (directory/(name+'.before.private')).write_bytes((ROOT/name).read_bytes())
            release.reconcile=lambda *_: {'deferred':'normal host timer owns unrelated resource retirement'}
            if args.role=='v2-projector':subprocess.run(['docker','stop','-t','10',CONTAINER],check=True)
            release.deploy(ROOT,state,manifest)
            receipt=dict(commit=commit,image=json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]['Image'])
        release.atomic(directory/(args.action+'.json'),receipt)
        print(json.dumps(receipt),flush=True)

if __name__=='__main__':main()
