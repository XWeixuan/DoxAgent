"""Pair the reviewed SDK/Native on current role images; use the formal release owner."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tomllib

ROOT=Path('/var/lib/doxagent/releases')
REPO='/home/ubuntu/doxagent'
ROLES=('codex-worker','v2-scheduler')
FILES=('deploy/Dockerfile.codex-sdk-upgrade','pyproject.toml','uv.lock')
RUNTIME=('codex','codex-code-mode-host','manifest.json','LICENSE.codex')


def git(commit,path):
    return subprocess.check_output(['git','-c','safe.directory='+REPO,'-C',REPO,'show',commit+':'+path])


def inspect(role):
    return json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]


def config_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


SOURCE_PROOF='''import hashlib,json,pathlib
r=pathlib.Path('/app/src/doxagent');h=hashlib.sha256()
for p in sorted(r.rglob('*.py')):
 h.update(str(p.relative_to(r)).encode());h.update(b'\\0');h.update(hashlib.sha256(p.read_bytes()).digest())
print(h.hexdigest())
'''


def validation_code(expected,version):
    return SOURCE_PROOF.replace('print(h.hexdigest())',f'assert h.hexdigest()=={expected!r}')+f'''
import importlib.metadata as m,subprocess
assert m.version('openai-codex')==m.version('openai-codex-cli-bin')=={version!r}
r=pathlib.Path('/opt/doxagent/codex-runtime');v=json.loads((r/'manifest.json').read_bytes())
assert v['upstream_version']=={version!r} and v['storage_local_root_exit'] is True
assert hashlib.sha256((r/'codex').read_bytes()).hexdigest()==v['binary_sha256']
assert hashlib.sha256((r/'codex-code-mode-host').read_bytes()).hexdigest()==v['companions']['codex-code-mode-host']['sha256']
assert {version!r} in subprocess.check_output([str(r/'codex'),'--version'],text=True)
print('SOURCE_SDK_NATIVE_OK')
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('build','deploy-worker','deploy-scheduler'))
    p.add_argument('commit')
    p.add_argument('--runtime',type=Path)
    a=p.parse_args();os.umask(0o077)
    directory=ROOT/('w3-sdk172-'+a.commit[:12]);directory.mkdir(exist_ok=True)
    owner=directory/'formal_owner.py';owner.write_bytes(git(a.commit,'scripts/ops/storage_release.py'))
    spec=importlib.util.spec_from_file_location('formal_release',owner)
    release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
    with release.lock(ROOT):
        state=json.loads((ROOT/'state.json').read_bytes())
        configuration=json.loads((ROOT/'compose.current.json').read_bytes())
        if a.action=='build':
            if a.runtime is None:raise ValueError('--runtime artifact directory is required')
            metadata=tomllib.loads(git(a.commit,'pyproject.toml').decode())
            version=next(d.split('==')[1] for d in metadata['project']['dependencies'] if d.startswith('openai-codex=='))
            native=json.loads((a.runtime/'manifest.json').read_bytes())
            assert native['upstream_version']==version=='0.172.0'
            assert native['patch_sha256']==hashlib.sha256(git(a.commit,'deploy/codex-patches/w3-time-awareness-'+version+'.patch')).hexdigest()
            assert native['storage_log_patch_sha256']==hashlib.sha256(git(a.commit,'deploy/codex-patches/storage-log-policy-'+version+'.patch')).hexdigest()
            assert native['storage_local_root_exit'] is True
            manifest=dict(commit=a.commit,compose_command=state['compose_base_command'],components={},baseline={})
            receipt=dict(commit=a.commit,sdk=version,native=native,roles={})
            for role in ROLES:
                info=inspect(role);assert info['Image']==state['components'][role]['current']['image']
                context=directory/role;context.mkdir(exist_ok=True)
                for name in FILES:
                    dest=context/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(git(a.commit,name))
                for name in RUNTIME:
                    dest=context/'deploy/codex-runtime'/name;dest.parent.mkdir(parents=True,exist_ok=True)
                    dest.write_bytes((a.runtime/name).read_bytes())
                digest=subprocess.check_output(['docker','exec',info['Id'],'python','-c',SOURCE_PROOF],text=True).strip()
                check=validation_code(digest,version)
                base='doxagent-sdk172-base:'+info['Image'][7:19]
                subprocess.run(['docker','tag',info['Image'],base],check=True)
                tag='doxagent-sdk172:'+a.commit[:12]+'-'+role
                subprocess.run(['docker','build','-f',str(context/'deploy/Dockerfile.codex-sdk-upgrade'),
                    '--build-arg','DOXAGENT_BASE_IMAGE='+base,
                    '--build-arg','DOXAGENT_RUNTIME_USER='+(info['Config'].get('User') or 'root'),
                    '-t',tag,str(context)],check=True)
                image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
                subprocess.run(['docker','run','--rm','--network','none','--memory','768m','--cpus','1',
                    '--entrypoint','python',image,'-c',check],check=True)
                candidate=copy.deepcopy(configuration['services'][role]);candidate['image']=image
                if role=='v2-scheduler':
                    env=candidate.setdefault('environment',{})
                    assert isinstance(env,dict),'formal role environment must be an explicit mapping'
                    env['DOXAGENT_W3_FAST_SOURCE_IDS']='sec_edgar,corporate_ir_press_releases'
                candidate['checks']=[['docker','exec','doxagent-v2-'+role+'-1','python','-c',check]]
                manifest['components'][role]=candidate
                manifest['baseline'][role]=dict(image=info['Image'],compose_sha256=config_hash(configuration['services'][role]))
                receipt['roles'][role]=dict(image=image,baseline=info['Image'],source_sha256=digest)
                state.setdefault('owned_images',[]).append(image)
                state.setdefault('candidates',[]).append(dict(image=image,expires=__import__('time').time()+48*3600))
            release.atomic(ROOT/'state.json',state)
            release.atomic(directory/'manifest.private.json',manifest)
            release.atomic(directory/'build.json',receipt)
            print(json.dumps(receipt),flush=True)
        else:
            role='codex-worker' if a.action=='deploy-worker' else 'v2-scheduler'
            manifest=json.loads((directory/'manifest.private.json').read_bytes())
            baseline=manifest['baseline'][role]
            assert inspect(role)['Image']==baseline['image'],'role image changed since candidate build'
            assert config_hash(configuration['services'][role])==baseline['compose_sha256'],'role configuration changed'
            manifest['components']={role:manifest['components'][role]}
            for name in ('state.json','compose.current.json'):
                (directory/(role+'.'+name+'.before.private')).write_bytes((ROOT/name).read_bytes())
            # The existing host timer owns resource retirement. Publication does
            # not add an unrelated full resource-inventory pass to its window.
            release.reconcile=lambda *_:{'deferred':'normal host resource timer'}
            release.deploy(ROOT,state,manifest)
            receipt=dict(commit=a.commit,role=role,at=__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat())
            release.atomic(directory/(role+'.deploy.json'),receipt)
            print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
