"""One-file API overlay with baseline guards and the canonical release owner."""

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
from pathlib import Path

ROOT = Path('/var/lib/doxagent/releases')
REPO = '/home/ubuntu/doxagent'
ROLE = 'v2-api'
CONTAINER = 'doxagent-v2-v2-api-1'
SOURCE = 'src/doxagent/api_v2/app.py'


def git(commit):
    return subprocess.check_output(['sudo', '-u', 'ubuntu', 'git', '-C', REPO,
                                    'show', commit + ':' + SOURCE])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'deploy'])
    parser.add_argument('commit')
    args = parser.parse_args()
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('release', ROOT / 'storage_release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    with release.lock(ROOT):
        state = json.loads((ROOT / 'state.json').read_bytes())
        directory = ROOT / ('catalog-budget-' + args.commit[:12])
        directory.mkdir(exist_ok=True)
        info = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER]))[0]
        assert info['Image'] == state['components'][ROLE]['current']['image']
        if args.action == 'build':
            blob = subprocess.check_output(['docker', 'cp', CONTAINER + ':/app/' + SOURCE, '-'])
            with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
                baseline = archive.extractfile(archive.getmembers()[0]).read()
            assert baseline == git(args.commit + '^'), 'deployed source drift'
            raw = git(args.commit)
            digest = hashlib.sha256(raw).hexdigest()
            target = directory / SOURCE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            check = ('import hashlib;assert hashlib.sha256(open("/app/' + SOURCE +
                     '","rb").read()).hexdigest()==' + repr(digest))
            base = 'doxagent-catalog-budget:base-' + info['Image'][7:19]
            subprocess.run(['docker', 'tag', info['Image'], base], check=True)
            (directory / 'Dockerfile').write_text('\n'.join([
                'FROM ' + base, 'USER root', 'COPY --chmod=0644 ' + SOURCE + ' /app/' + SOURCE,
                'RUN python -m compileall -q --invalidation-mode checked-hash /app/' + SOURCE,
                'USER ' + (info['Config'].get('User') or 'root'),
                'RUN python -c ' + shlex.quote(check), '']), encoding='utf-8')
            tag = 'doxagent-catalog-budget:' + args.commit[:12]
            subprocess.run(['docker', 'build', '-t', tag, str(directory)], check=True)
            image = subprocess.check_output(['docker', 'image', 'inspect', '--format',
                                             '{{.Id}}', tag], text=True).strip()
            config = json.loads((ROOT / 'compose.current.json').read_bytes())
            candidate = copy.deepcopy(config['services'][ROLE])
            candidate.update(image=image,
                             checks=[['docker', 'exec', CONTAINER, 'python', '-c', check]])
            manifest = dict(commit=args.commit, compose_command=state['compose_base_command'],
                            components={ROLE: candidate}, baseline=info['Image'])
            state.setdefault('owned_images', []).append(image)
            release.atomic(ROOT / 'state.json', state)
            release.atomic(directory / 'manifest.private.json', manifest)
            receipt = dict(commit=args.commit, role=ROLE, baseline=info['Image'], image=image,
                           source=SOURCE, sha256=digest)
            release.atomic(directory / 'build.json', receipt)
        else:
            manifest = json.loads((directory / 'manifest.private.json').read_bytes())
            assert info['Image'] == manifest['baseline'], 'another API release intervened'
            for name in ('state.json', 'compose.current.json'):
                (directory / (name + '.before.private')).write_bytes((ROOT / name).read_bytes())
            release.reconcile = lambda *_: {'deferred': 'normal host resource timer'}
            release.deploy(ROOT, state, manifest)
            receipt = dict(commit=args.commit, role=ROLE, deployed=True)
            release.atomic(directory / 'deploy.json', receipt)
        print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
