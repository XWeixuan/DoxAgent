"""Normal-window O2 and precise history overlays using the canonical component release owner."""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path('/var/lib/doxagent/releases')
REPO = '/home/ubuntu/doxagent'
FILES = ['src/doxagent/event_library/repository.py', 'src/doxagent/persistent_runtime_v2/bounded_inputs.py', 'src/doxagent/persistent_runtime_v2/coordinator.py', 'src/doxagent/persistent_runtime_v2/daily_index.py', 'src/doxagent/persistent_runtime_v2/history_exit.py', 'src/doxagent/persistent_runtime_v2/journal.py', 'src/doxagent/persistent_runtime_v2/maintenance.py', 'src/doxagent/persistent_runtime_v2/maintenance_recovery.py', 'src/doxagent/persistent_runtime_v2/repository.py', 'src/doxagent/persistent_runtime_v2/resource_gc.py', 'src/doxagent/persistent_runtime_v2/resource_refs.py', 'src/doxagent/persistent_runtime_v2/schema.py', 'src/doxagent/persistent_runtime_v2/service.py', 'src/doxagent/persistent_runtime_v2/w3_execution.py', 'src/doxagent/persistent_runtime_v2/worker_receipts.py', 'src/doxagent/runtime_scheduler/v2.py', 'src/doxagent/persistent_runtime_v2/normal_o2.py', 'scripts/ops/runtime_normal_o2_repair_20261127.py']
TRANSPORT_FILES = [p for p in FILES if p.endswith(('/event_library/repository.py','/journal.py','/resource_refs.py','/history_exit.py','/resource_gc.py'))]
ROLES = ['v2-scheduler','v2-transport-maintenance']


def git(commit, path):
    return subprocess.check_output(['sudo', '-u', 'ubuntu', 'git', '-C', REPO,
                                    'show', commit+':'+path])


def actual(container, path):
    raw = subprocess.check_output(['docker', 'cp', container+':/app/'+path, '-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return archive.extractfile(archive.getmembers()[0]).read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'deploy-scheduler','deploy-transport'])
    parser.add_argument('commit')
    args = parser.parse_args()
    os.umask(0o077)
    # Freeze the reviewed canonical owner from this commit. The host's old
    # copy lacks cooperative Scheduler cutover; never force-stop it instead.
    owner = ROOT/('normal-o2-owner-'+args.commit[:12]+'.py')
    owner.write_bytes(git(args.commit, 'scripts/ops/storage_release.py'))
    spec = importlib.util.spec_from_file_location('release', owner)
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    with release.lock(ROOT):
        state = json.loads((ROOT/'state.json').read_bytes())
        directory = ROOT/('normal-o2-'+args.commit[:12])
        directory.mkdir(exist_ok=True)
        if args.action == 'build':
            config = json.loads((ROOT/'compose.current.json').read_bytes())
            manifest = dict(commit=args.commit, compose_command=state['compose_base_command'],
                            components={}, baseline={})
            receipt = dict(commit=args.commit, roles={})
            for role in ROLES:
                container = 'doxagent-v2-'+role+'-1'
                info = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]
                assert info['Image'] == state['components'][role]['current']['image']
                context = directory/role
                hashes = {}
                role_files = FILES if role == 'v2-scheduler' else TRANSPORT_FILES
                for path in role_files:
                    exists = subprocess.run(['sudo','-u','ubuntu','git','-C',REPO,'cat-file','-e',args.commit+'^:'+path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
                    if exists:
                        assert actual(container, path) == git(args.commit+'^', path), path+' drift'
                    else:
                        assert subprocess.check_output(['docker','exec',container,'python','-c',
                            'import os;print(os.path.isfile('+repr('/app/'+path)+'))'],text=True).strip() == 'False'
                    raw = git(args.commit, path)
                    target = context/path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    hashes[path] = hashlib.sha256(raw).hexdigest()
                check = ('import hashlib;expected='+repr(hashes)+';assert all('
                         'hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest()==h '
                         'for p,h in expected.items())')
                base = 'doxagent-event-recovery:base-'+info['Image'][7:19]
                subprocess.run(['docker', 'tag', info['Image'], base], check=True)
                recipe = ['FROM '+base, 'USER root']
                recipe += ['COPY --chmod=0644 '+p+' /app/'+p for p in role_files]
                recipe += ['RUN python -m compileall -q --invalidation-mode checked-hash '+
                           ' '.join('/app/'+p for p in role_files),
                           'USER '+(info['Config'].get('User') or 'root'),
                           'RUN python -c '+shlex.quote(check)]
                (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
                tag = 'doxagent-normal-o2:'+args.commit[:12]+'-'+role
                subprocess.run(['docker', 'build', '-t', tag, str(context)], check=True)
                image = subprocess.check_output(['docker', 'image', 'inspect', '--format',
                                                 '{{.Id}}', tag], text=True).strip()
                candidate = copy.deepcopy(config['services'][role])
                candidate.update(image=image,
                    checks=[['docker', 'exec', container, 'python', '-c', check]])
                manifest['components'][role] = candidate
                manifest['baseline'][role] = info['Image']
                receipt['roles'][role] = dict(image=image, baseline=info['Image'], hashes=hashes)
                state.setdefault('owned_images', []).append(image)
                state.setdefault('candidates', []).append(dict(image=image, expires=time.time()+48*3600))
            release.atomic(ROOT/'state.json', state)
            release.atomic(directory/'manifest.private.json', manifest)
            release.atomic(directory/'build.json', receipt)
        else:
            manifest = json.loads((directory/'manifest.private.json').read_bytes())
            selected = 'v2-scheduler' if args.action=='deploy-scheduler' else 'v2-transport-maintenance'
            manifest['components'] = {selected:manifest['components'][selected]}
            for role, baseline in manifest['baseline'].items():
                if role != selected:continue
                info = json.loads(subprocess.check_output(['docker', 'inspect',
                    'doxagent-v2-'+role+'-1']))[0]
                assert info['Image'] == baseline, 'another release changed '+role
            for name in ('state.json', 'compose.current.json'):
                (directory/(name+'.before.private')).write_bytes((ROOT/name).read_bytes())
            release.reconcile = lambda *_: {'deferred': 'normal host resource timer'}
            release.deploy(ROOT, state, manifest)
            receipt = dict(commit=args.commit, roles=list(manifest['components']), at=time.time())
            release.atomic(directory/'deploy.json', receipt)
        print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
