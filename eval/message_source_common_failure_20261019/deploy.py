"""Narrow Bus/enrichment release; preserve all browser containers and data mounts."""
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
commit = sys.argv[1]
expected_base = sys.argv[2]
services = ['v2-message-bus', 'v2-content-enrichment']
files = [
    'message_bus_v2/storage_lane.py', 'message_bus_v2/repository.py',
    'message_bus_v2/service.py', 'message_bus_v2/scheduler.py',
    'message_bus_v2/distribution.py', 'persistent_runtime_v2/bus_orchestration.py',
    'content_enrichment/service.py',
    'message_bus_v2/ibkr_news.py', 'message_bus_v2/market_sources.py',
    'message_bus_v2/manifests.py', 'site_strategy/client.py',
]
os.environ.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=str(repo))


def run(args, timeout=180):
    p = subprocess.run(args, cwd=repo, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(str(args[:3]) + ': ' + p.stderr[-1500:])
    return p.stdout.strip()


def inventory():
    ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=doxagent-v2']).split()
    return {c['Config']['Labels']['com.docker.compose.service']: c for c in json.loads(run(['docker', 'inspect', *ids]))}


spec = importlib.util.spec_from_file_location('release', repo/'scripts/ops/storage_release.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with m.lock(root):
    assert run(['git', 'rev-parse', 'HEAD']) == commit
    assert not run(['git', 'status', '--porcelain', '--untracked-files=no'])
    before = inventory()
    assert all(before[s]['Image'] == expected_base for s in services), 'live base changed; review first'
    audit = root / ('bus-loop-' + commit[:12])
    audit.mkdir(exist_ok=False)
    canonical = root/'compose.current.json'
    state = json.loads((root/'state.json').read_text())
    cfg = json.loads(canonical.read_text())
    m.atomic(audit/'compose.before.private.json', cfg)
    m.atomic(audit/'state.before.private.json', state)
    os.chmod(audit/'compose.before.private.json', 0o600)
    os.chmod(audit/'state.before.private.json', 0o600)
    alias = 'doxagent-bus-base:' + expected_base.removeprefix('sha256:')[:12]
    tag = 'doxagent-v2:bus-loop-' + commit[:12]
    run(['docker', 'tag', expected_base, alias])
    base_commit = dict(item.split('=', 1) for item in before[services[0]]['Config']['Env'])['DOXAGENT_BUILD_COMMIT']
    run(['docker', 'build', '-f', 'deploy/Dockerfile.bus-storage-repair',
         '--build-arg', 'APPROVED_BASE='+alias, '--build-arg', 'REPAIR_COMMIT='+commit,
         '--build-arg', 'BASE_COMMIT='+base_commit,
         '-t', tag, '.'], timeout=600)
    image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag])
    state.setdefault('owned_images', []).append(image)
    state.setdefault('candidates', []).append({'image': image, 'expires': time.time()+48*3600})
    m.atomic(root/'state.json', state)
    base_command = state['compose_base_command']
    command = [*base_command, '-f', str(canonical)]
    expected = {f: hashlib.sha256((repo/'src/doxagent'/f).read_bytes()).hexdigest() for f in files}
    for service in services:
        candidate = dict(cfg['services'].get(service, {}))
        candidate['image'] = image
        candidate.setdefault('environment', {})['DOXAGENT_BUILD_COMMIT'] = commit
        cfg['services'][service] = candidate
    candidate_file = audit/'compose.next.private.json'
    m.atomic(candidate_file, cfg)
    os.chmod(candidate_file, 0o600)
    run([*base_command, '-f', str(candidate_file), 'config', '--quiet'])
    started = time.time()
    try:
        # Old code may still have synchronous owner work. Do not SIGKILL a DB writer.
        for service in services:
            print('Gracefully stopping '+service, flush=True)
            run(['docker', 'stop', '--time', '-1', before[service]['Id']], timeout=600)
        m.atomic(canonical, cfg)
        for service in services:
            print('Starting '+service, flush=True)
            run([*command, 'up', '-d', '--no-deps', '--no-build', service])
            time.sleep(6)
            after = inventory()
            c = after[service]
            assert c['State']['Running'] and c['Image'] == image
            check = "import json,hashlib;from pathlib import Path;p=Path('/app/src/doxagent');print(json.dumps({f:hashlib.sha256((p/f).read_bytes()).hexdigest() for f in "+repr(files)+"}))"
            assert json.loads(run(['docker', 'exec', c['Id'], 'python', '-c', check])) == expected
            mounts = lambda item: sorted((v['Destination'], v['Type'], v['Source'], v['RW']) for v in item['Mounts'])
            assert mounts(c) == mounts(before[service]), 'persistent mounts changed'
            m.promote(state, service, image, commit=commit)
            state['components'][service]['current']['compose'] = cfg['services'][service]
            m.atomic(root/'state.json', state)
        after = inventory()
        for service in before:
            if service not in services:
                assert before[service]['Id'] == after[service]['Id'], service+' unexpectedly recreated'
        receipt = dict(commit=commit, image=image, started=started, finished=time.time(),
                       files_sha256=expected, services=services, no_cleanup=True,
                       preserved_services=[s for s in before if s not in services],
                       before={s: {'id': before[s]['Id'], 'image': before[s]['Image']} for s in services},
                       after={s: {'id': after[s]['Id'], 'image': after[s]['Image']} for s in services})
        m.atomic(audit/'release.json', receipt)
        print(json.dumps(receipt), flush=True)
    except BaseException:
        m.atomic(canonical, json.loads((audit/'compose.before.private.json').read_text()))
        m.atomic(root/'state.json', json.loads((audit/'state.before.private.json').read_text()))
        subprocess.run([*command, 'up', '-d', '--no-deps', '--no-build', *services], cwd=repo, check=True)
        raise
