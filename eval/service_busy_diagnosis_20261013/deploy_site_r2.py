"""Complete the current-entry ownership repair, only Site Access is recreated."""
import json
import subprocess
from pathlib import Path

root = Path('/home/ubuntu/service-busy-repair-20261013')


def run(args, timeout=120):
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-2000:])
    return p.stdout


def inspect(name):
    return json.loads(run(['docker', 'inspect', name]))[0]


name = 'doxagent-v2-v2-site-access-1'
before = inspect(name)
assert before['Config']['Image'] == 'doxagent-site-access:lifecycle-20261013-r1'
untouched = {n: inspect(n)['Id'] for n in run(['docker', 'ps', '--format', '{{.Names}}']).split() if n != name}
tag = 'doxagent-site-access:lifecycle-20261013-r2'
run(['docker', 'build', '-t', tag, '-f', str(root / 'eval/service_busy_diagnosis_20261013/Dockerfile.site.r2'), str(root)], timeout=180)
overlay = root / 'v2-site-access-r2.yaml'
overlay.write_text('services:\n  v2-site-access:\n    image: ' + tag + '\n')
compose = ['docker', 'compose', '-p', 'doxagent-v2', '--env-file', '/home/ubuntu/doxagent/.env.v2']
for path in before['Config']['Labels']['com.docker.compose.project.config_files'].split(',') + [str(overlay)]:
    compose += ['-f', path]
run(compose + ['config', '--quiet'])
print('recreating Site Access only', flush=True)
run(compose + ['up', '-d', '--no-deps', '--no-build', '-t', '20', 'v2-site-access'])
after = inspect(name)
assert after['Config']['Image'] == tag
assert set(before['Config']['Env']) == set(after['Config']['Env'])
assert sorted(before['Mounts'], key=lambda m: m['Destination']) == sorted(after['Mounts'], key=lambda m: m['Destination'])
assert all(inspect(n)['Id'] == identity for n, identity in untouched.items())
path = root / 'release.json'
receipt = json.loads(path.read_text())
receipt['services']['v2-site-access']['r1_after'] = receipt['services']['v2-site-access']['after']
receipt['services']['v2-site-access']['after'] = {'id': after['Id'], 'image': after['Image'], 'tag': tag, 'started': after['State']['StartedAt']}
receipt['services']['v2-site-access']['r2_overlay'] = str(overlay)
path.write_text(json.dumps(receipt, indent=2))
path.chmod(0o600)
print(json.dumps({'site_access': receipt['services']['v2-site-access']['after'], 'unrelated_containers_unchanged': True}), flush=True)
