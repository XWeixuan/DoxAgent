"""Narrow immutable overlays, promoted by the canonical component release owner."""
import argparse
import copy
import hashlib
import importlib.util
import json
import shlex
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path('/var/lib/doxagent/releases')
REPO = Path('/home/ubuntu/doxagent')
ROLES = ['v2-web', 'v2-api', 'v2-projector', 'v2-executor']
FILES = ['src/doxagent/trade_execution/'+name for name in (
    'executor.py','repository.py','groups.py','worker.py','cli.py','recovery.py',
    'ibkr_session.py','ownership.py','exit_guard.py','management.py')]
FILES += ['src/doxagent/exit_strategy/schema.py','src/doxagent/api_v2/wire_schema.json',
          'src/doxagent/v2_read/executions.py']


def run(args):
    return subprocess.check_output(args, text=True).strip()


def git(commit, path):
    return subprocess.check_output(['sudo','-u','ubuntu','git','-C',str(REPO),'show',commit+':'+path])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['build','deploy'])
    parser.add_argument('--commit',required=True)
    parser.add_argument('--web-archive',type=Path)
    args=parser.parse_args()
    commit=run(['sudo','-u','ubuntu','git','-C',str(REPO),'rev-parse',args.commit])
    assert len(commit)==40
    directory=ROOT/('dynamic-ownership-'+commit[:12])
    directory.mkdir(mode=0o700,exist_ok=True)
    owner=directory/'storage_release.py'
    owner.write_bytes(git(commit,'scripts/ops/storage_release.py'))
    spec=importlib.util.spec_from_file_location('release',owner)
    release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
    with release.lock(ROOT):
        state=json.loads((ROOT/'state.json').read_text())
        if args.action=='build':
            if 'v2-web' in ROLES:
                assert args.web_archive and args.web_archive.is_file()
            config=json.loads((ROOT/'compose.current.json').read_text())
            manifest=dict(commit=commit,compose_command=state['compose_base_command'],components={},baselines={},hashes={})
            for role in ROLES:
                container='doxagent-v2-'+role+'-1'
                info=json.loads(run(['docker','inspect',container]))[0]
                assert info['Image']==state['components'][role]['current']['image']
                manifest['baselines'][role]=info['Image']
                context=directory/('build-'+role);context.mkdir(exist_ok=True)
                base='doxagent-v2:ownership-base-'+role+'-'+info['Image'][7:19]
                subprocess.run(['docker','tag',info['Image'],base],check=True)
                recipe=['FROM '+base,'USER root','LABEL org.opencontainers.image.revision='+commit]
                hashes={}
                if role=='v2-web':
                    web=context/'web';web.mkdir(exist_ok=True)
                    with tarfile.open(args.web_archive) as archive:
                        for member in archive.getmembers():
                            assert not member.issym() and not member.islnk()
                            assert (web/member.name).resolve().is_relative_to(web.resolve())
                        archive.extractall(web)
                    recipe+=['COPY web/ /usr/share/nginx/html/',
                             'RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + && find /usr/share/nginx/html -type f -exec chmod 644 {} +']
                    hashes={str(p.relative_to(web)):hashlib.sha256(p.read_bytes()).hexdigest() for p in web.rglob('*') if p.is_file()}
                    check=['docker','exec',container,'wget','-q','-T','3','--spider','http://127.0.0.1/index.html']
                else:
                    for path in FILES:
                        raw=git(commit,path);target=context/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
                        hashes[path]=hashlib.sha256(raw).hexdigest()
                        recipe+=['COPY --chmod=0644 '+path+' /app/'+path]
                    recipe+=['RUN umask 022 && python -m compileall -q --invalidation-mode checked-hash /app/src/doxagent/trade_execution /app/src/doxagent/exit_strategy /app/src/doxagent/v2_read']
                    code='import hashlib; expected='+repr(hashes)+'; assert all(hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest()==h for p,h in expected.items())'
                    recipe+=['RUN python -c '+shlex.quote(code)]
                    check=['docker','exec',container,'python','-c',code]
                recipe+=['USER '+(info['Config'].get('User') or 'root')]
                (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
                tag='doxagent-v2:ownership-'+commit[:12]+'-'+role
                subprocess.run(['docker','build','-t',tag,str(context)],check=True)
                image=run(['docker','image','inspect','--format','{{.Id}}',tag])
                candidate=copy.deepcopy(config['services'][role]);candidate.update(image=image,checks=[check])
                manifest['components'][role]=candidate;manifest['hashes'][role]=hashes
                state.setdefault('owned_images',[]).append(image)
            release.atomic(ROOT/'state.json',state)
            release.atomic(directory/'manifest.private.json',manifest)
            print(json.dumps(dict(commit=commit,roles={k:v['image'] for k,v in manifest['components'].items()})))
        else:
            manifest=json.loads((directory/'manifest.private.json').read_text())
            for role,baseline in manifest['baselines'].items():
                assert json.loads(run(['docker','inspect','doxagent-v2-'+role+'-1']))[0]['Image'] in {baseline, manifest['components'][role]['image']}
            for name in ('state.json','compose.current.json'):
                (directory/(name+'.before.private')).write_bytes((ROOT/name).read_bytes())
            release.reconcile=lambda *_:{'deferred':'normal host timer owns unrelated resource retirement'}
            for role in ROLES:
                current = json.loads(run(['docker','inspect','doxagent-v2-'+role+'-1']))[0]['Image']
                if current == manifest['components'][role]['image'] and state['components'][role]['current']['image'] == current:
                    continue
                if role=='v2-executor':
                    query = "import sqlite3; d=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True); d.execute('pragma query_only=ON'); print(d.execute(\"select count(*) from te_attempts where state not in ('SETTLED','NOT_SENT')\").fetchone()[0])"
                    for _ in range(10):
                        if int(run(['docker','exec','doxagent-v2-v2-executor-1','python','-c',query])) == 0:
                            break
                        time.sleep(2)
                    else:
                        raise RuntimeError('active strategy orders: executor cutover deferred, no orders cancelled')
                    subprocess.run(['docker','stop','-t','30','doxagent-v2-v2-executor-1'],check=True)
                one=dict(manifest,components={role:manifest['components'][role]})
                release.deploy(ROOT,state,one)
            receipt=dict(commit=commit,roles={role:json.loads(run(['docker','inspect','doxagent-v2-'+role+'-1']))[0]['Image'] for role in ROLES})
            release.atomic(directory/'deployed.json',receipt)
            print(json.dumps(receipt))


if __name__=='__main__':
    main()
