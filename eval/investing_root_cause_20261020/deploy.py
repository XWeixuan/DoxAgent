"""Exact-parent Site Access update; native Supervisor and all other services stay intact."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

repo = Path('/home/ubuntu/doxagent')
root = Path('/var/lib/doxagent/releases')
commit, base = sys.argv[1:3]
os.environ.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=str(repo))
files = ['content_enrichment/quality.py', 'site_strategy/governance_repository.py',
         'site_strategy/service.py', 'site_strategy/verification.py',
         'site_strategy/isolation_migration.py', 'site_strategy/runtime.py',
         'site_strategy/investing_document.py', 'site_strategy/worker_runtime.py',
         'site_strategy/maintenance.py', 'site_strategy/worker_manager.py']


def run(args, timeout=180):
    p = subprocess.run(args, cwd=repo, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(str(args[:3]) + ': ' + p.stderr[-900:])
    return p.stdout.strip()


def inventory():
    ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=doxagent-v2']).split()
    return {c['Config']['Labels']['com.docker.compose.service']: c
            for c in json.loads(run(['docker', 'inspect', *ids]))}


spec = importlib.util.spec_from_file_location('release', repo / 'scripts/ops/storage_release.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with m.lock(root):
    assert run(['git', 'rev-parse', 'HEAD']) == commit
    before = inventory()
    assert before['v2-site-access']['Image'] == base
    canonical = root / 'compose.current.json'
    state = json.loads((root / 'state.json').read_text())
    cfg = json.loads(canonical.read_text())
    audit = root / ('investing-' + commit[:12])
    audit.mkdir(exist_ok=False)
    for name, value in [('compose.before.private.json', cfg), ('state.before.private.json', state)]:
        m.atomic(audit / name, value)
        os.chmod(audit / name, 0o600)
    alias = 'doxagent-base:' + base.removeprefix('sha256:')[:12]
    tag = 'doxagent-v2-site-access:investing-' + commit[:12]
    run(['docker', 'tag', base, alias])
    run(['docker', 'build', '-f', 'deploy/Dockerfile.investing-recovery',
         '--build-arg', 'APPROVED_BASE=' + alias, '--build-arg', 'REPAIR_COMMIT=' + commit,
         '-t', tag, '.'], timeout=600)
    image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag])
    state.setdefault('owned_images', []).append(image)
    state.setdefault('candidates', []).append({'image': image, 'expires': time.time() + 48 * 3600})
    cfg['services']['v2-site-access']['image'] = image
    cfg['services']['v2-site-access']['environment']['DOXAGENT_BUILD_COMMIT'] = commit
    m.atomic(root / 'state.json', state)
    command = [*state['compose_base_command'], '-f', str(canonical)]
    try:
        run(['docker', 'stop', '--time', '-1', before['v2-site-access']['Id']], timeout=240)
        m.atomic(canonical, cfg)
        run([*command, 'up', '-d', '--no-deps', '--no-build', 'v2-site-access'])
        for _ in range(60):
            after = inventory()
            if after['v2-site-access']['State'].get('Health', {}).get('Status') == 'healthy':
                break
            time.sleep(2)
        else:
            raise RuntimeError('readiness failed')
        assert all(before[n]['Id'] == after[n]['Id'] for n in before if n != 'v2-site-access')
        assert os.readlink(f"/proc/{after['v2-site-access']['State']['Pid']}/ns/net") == os.readlink(
            f"/proc/{after['v2-chrome-supervisor']['State']['Pid']}/ns/net")
        expected = {f: hashlib.sha256((repo / 'src/doxagent' / f).read_bytes()).hexdigest() for f in files}
        code = 'import hashlib,json;from pathlib import Path;p=Path("/app/src/doxagent");print(json.dumps({f:hashlib.sha256((p/f).read_bytes()).hexdigest() for f in ' + repr(files) + '}))'
        assert json.loads(run(['docker', 'exec', after['v2-site-access']['Id'], 'python', '-c', code])) == expected
        m.promote(state, 'v2-site-access', image, commit=commit)
        state['components']['v2-site-access']['current']['compose'] = cfg['services']['v2-site-access']
        m.atomic(root / 'state.json', state)
        receipt = {'commit': commit, 'base': base, 'image': image, 'hashes': expected,
                   'other_containers_unchanged': True, 'network_namespace_matches': True,
                   'finished': time.time()}
        m.atomic(audit / 'release.json', receipt)
        print(json.dumps(receipt))
    except BaseException:
        m.atomic(canonical, json.loads((audit / 'compose.before.private.json').read_text()))
        m.atomic(root / 'state.json', json.loads((audit / 'state.before.private.json').read_text()))
        subprocess.run([*command, 'up', '-d', '--no-deps', '--no-build', 'v2-site-access'], cwd=repo, check=True)
        raise
