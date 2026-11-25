"""Three-file Event evidence overlays using the canonical component release owner."""
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
FILES = ['src/doxagent/event_library/admission_evidence.py',
         'src/doxagent/persistent_runtime_v2/event_branch.py',
         'src/doxagent/persistent_runtime_v2/retained_events.py']
ROLES = ['v2-transport-maintenance', 'v2-scheduler']


def git(commit, path):
    return subprocess.check_output(['sudo', '-u', 'ubuntu', 'git', '-C', REPO,
                                    'show', commit+':'+path])


def actual(container, path):
    raw = subprocess.check_output(['docker', 'cp', container+':/app/'+path, '-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return archive.extractfile(archive.getmembers()[0]).read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'deploy'])
    parser.add_argument('commit')
    args = parser.parse_args()
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('release', ROOT/'storage_release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    with release.lock(ROOT):
        state = json.loads((ROOT/'state.json').read_bytes())
        directory = ROOT/('event-evidence-'+args.commit[:12])
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
                for path in FILES:
                    assert actual(container, path) == git(args.commit+'^', path), path+' drift'
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
                recipe += ['COPY --chmod=0644 '+p+' /app/'+p for p in FILES]
                recipe += ['RUN python -m compileall -q --invalidation-mode checked-hash '+
                           ' '.join('/app/'+p for p in FILES),
                           'USER '+(info['Config'].get('User') or 'root'),
                           'RUN python -c '+shlex.quote(check)]
                (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
                tag = 'doxagent-event-recovery:'+args.commit[:12]+'-'+role
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
            for role, baseline in manifest['baseline'].items():
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
