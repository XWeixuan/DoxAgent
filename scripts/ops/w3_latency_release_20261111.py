"""Reviewed W3 overlays on accepted images; reuse the canonical release owner."""
import argparse
import copy
from datetime import UTC, datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import tarfile
import time

ROOT = Path('/var/lib/doxagent/releases')
REPO = Path('/home/ubuntu/doxagent')


def git(commit, path):
    return subprocess.check_output(['sudo', '-u', 'ubuntu', 'git', '-C', str(REPO),
                                    'show', commit + ':' + path])


def inspect(role):
    return json.loads(subprocess.check_output(['docker', 'inspect', 'doxagent-v2-' + role + '-1']))[0]


def source(role, path):
    raw = subprocess.check_output(['docker', 'cp', 'doxagent-v2-' + role + '-1:/app/' + path, '-'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return archive.extractfile(archive.getmembers()[0]).read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'deploy'])
    parser.add_argument('--commit', required=True)
    parser.add_argument('--proof', type=Path)
    parser.add_argument('--roles', nargs='+', choices=['codex-worker', 'v2-scheduler'])
    args = parser.parse_args()
    os.umask(0o077)
    commit = subprocess.check_output(['sudo', '-u', 'ubuntu', 'git', '-C', str(REPO),
                                      'rev-parse', args.commit], text=True).strip()
    if len(commit) != 40:
        raise ValueError('full source commit required')
    audit = ROOT / ('w3-latency-' + commit[:12])
    audit.mkdir(mode=0o700, exist_ok=True)
    owner = audit / 'storage_release.py'
    owner.write_bytes(git(commit, 'scripts/ops/storage_release.py'))
    spec = importlib.util.spec_from_file_location('release', owner)
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    with release.lock(ROOT):
        state = json.loads((ROOT / 'state.json').read_bytes())
        if args.action == 'build':
            proof = json.loads(args.proof.read_bytes())['roles']
            if args.roles:
                proof = {r: proof[r] for r in args.roles}
            conf = json.loads((ROOT / 'compose.current.json').read_bytes())
            manifest = dict(commit=commit, compose_command=state['compose_base_command'],
                            components={}, baseline={})
            receipt = dict(commit=commit, at=datetime.now(UTC).isoformat(), roles={})
            for role, previous in proof.items():
                info = inspect(role)
                if info['Image'] != previous['image'] or info['Image'] != state['components'][role]['current']['image']:
                    raise ValueError('accepted production image changed: ' + role)
                context = audit / ('build-' + role)
                context.mkdir(exist_ok=True)
                hashes = {}
                for path, expected in previous['files'].items():
                    if not path.startswith('src/doxagent/') or '..' in path:
                        raise ValueError('unexpected source path')
                    if hashlib.sha256(source(role, path)).hexdigest() != expected:
                        raise ValueError('reviewed source changed: ' + role + ' ' + path)
                    raw = git(commit, path)
                    target = context / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    hashes[path] = hashlib.sha256(raw).hexdigest()
                compiler = 'scripts/ops/compile_w3_imports.py'
                (context / 'compile_w3_imports.py').write_bytes(git(commit, compiler))
                base = 'doxagent-v2:w3-base-' + info['Image'][7:19]
                subprocess.run(['docker', 'tag', info['Image'], base], check=True)
                recipe = ['FROM ' + base, 'USER root']
                recipe += ['COPY --chmod=0644 ' + p + ' /app/' + p for p in hashes]
                recipe += ['COPY --chmod=0644 compile_w3_imports.py /tmp/compile_w3_imports.py',
                           'RUN umask 022 && python /tmp/compile_w3_imports.py',
                           'USER ' + (info['Config'].get('User') or 'root')]
                check = 'import hashlib;' + ''.join(
                    'assert hashlib.sha256(open(' + repr('/app/' + p) + ',"rb").read()).hexdigest()==' + repr(h) + ';'
                    for p, h in hashes.items())
                recipe += ['RUN python -c ' + shlex.quote(check)]
                (context / 'Dockerfile').write_text('\n'.join(recipe) + '\n')
                tag = 'doxagent-v2:w3-' + commit[:12] + '-' + role
                subprocess.run(['docker', 'build', '-t', tag, str(context)], check=True)
                image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
                if image not in state.setdefault('owned_images', []):
                    state['owned_images'].append(image)
                state.setdefault('candidates', []).append(dict(image=image, expires=time.time()+48*3600))
                candidate = copy.deepcopy(conf['services'][role])
                candidate['image'] = image
                candidate.setdefault('environment', {})['DOXAGENT_RUNTIME_REPAIR_COMMIT'] = commit
                candidate['checks'] = [['docker', 'exec', 'doxagent-v2-' + role + '-1', 'python', '-c', check]]
                manifest['components'][role] = candidate
                manifest['baseline'][role] = info['Image']
                receipt['roles'][role] = dict(previous=info['Image'], image=image, files=hashes)
                release.atomic(ROOT / 'state.json', state)
            release.atomic(audit / 'manifest.private.json', manifest)
            release.atomic(audit / 'build.json', receipt)
            print(json.dumps(receipt), flush=True)
        else:
            manifest = json.loads((audit / 'manifest.private.json').read_bytes())
            if args.roles:
                manifest['components'] = {r: manifest['components'][r] for r in args.roles}
            for role in manifest['components']:
                if inspect(role)['Image'] != manifest['baseline'][role]:
                    raise ValueError('another publication changed the accepted image: ' + role)
            if 'codex-worker' in manifest['components']:
                db = sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3?mode=ro', uri=True, timeout=.1)
                try:
                    if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                        raise ValueError('Worker has accepted work; wait for a natural idle publication window')
                finally:
                    db.close()
            for name in ['state.json', 'compose.current.json']:
                (audit / (name + '.before.private')).write_bytes((ROOT / name).read_bytes())
            for entry in inspect('initialization-guardian')['Config'].get('Env', []):
                key, _, value = entry.partition('=')
                if key.startswith('DOXAGENT_INITIALIZATION_REPAIR_'):
                    os.environ[key] = value
            manifest['components'] = {r: manifest['components'][r] for r in ['codex-worker', 'v2-scheduler'] if r in manifest['components']}
            release.reconcile = lambda *_: dict(deferred='observe realtime business before image retirement')
            release.deploy(ROOT, state, manifest)
            receipt = dict(commit=commit, roles=list(manifest['components']), at=datetime.now(UTC).isoformat())
            release.atomic(audit / 'release.json', receipt)
            print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
