"""Narrow image overlays and component promotion; no resource retirement."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

repo = Path('/home/ubuntu/doxagent')
root = Path('/var/lib/doxagent/releases')
audit = root / 'execution-queue-repair-20261028'
commit = sys.argv[1]
services = ['v2-executor']
files = ['trade_execution/recovery.py', 'trade_execution/executor.py', 'trade_execution/worker.py', 'trade_execution/repository.py']


def run(args, text=None, timeout=120):
    result = subprocess.run(args, cwd=repo, input=text, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(str(args[:3]) + '\n' + result.stderr[-3000:])
    return result.stdout.strip()


def inventory():
    ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=doxagent-v2']).split()
    return {c['Config']['Labels']['com.docker.compose.service']: c
            for c in json.loads(run(['docker', 'inspect', *ids]))}


spec = importlib.util.spec_from_file_location('release', repo / 'scripts/ops/storage_release.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with m.lock(root):
    assert run(['git', '-c', 'safe.directory=' + str(repo), 'rev-parse', 'HEAD']) == commit
    audit.mkdir(mode=0o700, exist_ok=True)
    if '--deploy' not in sys.argv:
        assert shutil.disk_usage(root).free > 5 * 1024**3
        current = inventory()
        manifest = {'commit': commit, 'components': {}, 'files': {}}
        context = audit / 'context'
        for name in files:
            source = repo / 'src/doxagent' / name
            target = context / 'src/doxagent' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest['files'][name] = hashlib.sha256(source.read_bytes()).hexdigest()
        patch_hash = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest()
        groups = {}
        for service in services:
            base = current[service]['Image']
            if base not in groups:
                tag = 'doxagent-v2:execution-queue-20261028-' + base[7:19]
                base_tag = 'doxagent-v2:trade-base-' + base[7:19]
                run(['docker', 'tag', base, base_tag])
                recipe = 'FROM ' + base_tag + '\n'
                recipe += 'LABEL doxagent.owner=doxagent doxagent.patch.commit=' + commit + '\n'
                recipe += 'LABEL doxagent.patch.sha256=' + patch_hash + '\n'
                recipe += 'COPY src/ /app/src/\n'
                (context / 'Dockerfile').write_text(recipe)
                run(['docker', 'build', '--network=none', '-t', tag, str(context)], timeout=180)
                groups[base] = run(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'])
            manifest['components'][service] = {'before': base, 'image': groups[base]}
        # Import/compile against the real immutable base, without network or live data.
        run(['docker', 'run', '--rm', '--network=none', '--entrypoint', 'python',
             groups[base], '-c',
             'import doxagent.trade_execution.worker, doxagent.trade_execution.executor; '
             'from doxagent.trade_execution.recovery import ENTRY_MAX_QUEUE_SECONDS; '
             'assert ENTRY_MAX_QUEUE_SECONDS == 300; print("candidate_import_ok")'], timeout=60)
        m.atomic(audit / 'manifest.json', manifest)
        print(json.dumps(manifest))
    else:
        manifest = json.loads((audit / 'manifest.json').read_text())
        state = json.loads((root / 'state.json').read_text())
        cfg = json.loads((root / 'compose.current.json').read_text())
        current = inventory()
        for service, value in manifest['components'].items():
            assert current[service]['Image'] == value['before'], (service, 'changed since build')
        m.atomic(audit / 'state.before.private.json', state)
        m.atomic(audit / 'compose.before.private.json', cfg)
        m.atomic(audit / 'containers.before.private.json', current)
        command = [*state['compose_base_command'], '-f', str(root / 'compose.current.json')]
        accepted = {}
        for service in services:
            candidate = manifest['components'][service]
            old = copy.deepcopy(cfg['services'][service])
            cfg['services'][service]['image'] = candidate['image']
            m.atomic(root / 'compose.current.json', cfg)
            try:
                run([*command, 'up', '-d', '--no-deps', '--no-build', service], timeout=180)
                deadline = time.monotonic() + 90
                while True:
                    info = inventory()[service]
                    health = info['State'].get('Health', {}).get('Status')
                    if info['State']['Running'] and info['Image'] == candidate['image'] and health in {None, 'healthy'}:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('candidate unhealthy: ' + service)
                    time.sleep(2)
            except BaseException:
                cfg['services'][service] = old
                m.atomic(root / 'compose.current.json', cfg)
                run([*command, 'up', '-d', '--no-deps', '--no-build', service], timeout=180)
                raise
            m.promote(state, service, candidate['image'], commit=commit,
                      schema=state['components'][service]['current'].get('schema'))
            state['components'][service]['current']['compose'] = copy.deepcopy(cfg['services'][service])
            state['components'][service]['current']['patch_files'] = manifest['files']
            state.setdefault('owned_images', []).append(candidate['image'])
            m.atomic(root / 'state.json', state)
            accepted[service] = {'image': info['Image'], 'started_at': info['State']['StartedAt'],
                                 'health': health, 'restarts': info['RestartCount']}
            print(json.dumps({'promoted': service, **accepted[service]}), flush=True)
        m.atomic(audit / 'release.json', {'commit': commit, 'components': accepted})
