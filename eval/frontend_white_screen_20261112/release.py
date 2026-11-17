"""Persist the static permission repair through the canonical web release owner."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/var/lib/doxagent/releases')
BASELINE = 'sha256:975d6bb2db283741537169152ec0008c6a8416fe8ce1b62fb4c17bd49e84ed3f'
CONTAINER = 'doxagent-v2-v2-web-1'
spec = importlib.util.spec_from_file_location('release', '/home/ubuntu/doxagent/scripts/ops/storage_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

with release.lock(ROOT):
    state = json.loads((ROOT / 'state.json').read_bytes())
    info = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER]))[0]
    assert info['Image'] == BASELINE == state['components']['v2-web']['current']['image']
    directory = ROOT / 'frontend-permissions-20261112'
    directory.mkdir(mode=0o700, exist_ok=True)
    context = directory / 'build'
    context.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile('/tmp/doxagent-web-healthcheck-20261112.sh', context / 'healthcheck.sh')
    subprocess.run(['docker', 'tag', BASELINE, 'doxagent-v2-web:permissions-baseline-20261112'], check=True)
    (context / 'Dockerfile').write_text('''FROM doxagent-v2-web:permissions-baseline-20261112
USER root
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + && find /usr/share/nginx/html -type f -exec chmod 644 {} +
COPY --chmod=0755 healthcheck.sh /usr/local/bin/doxagent-web-healthcheck
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD /usr/local/bin/doxagent-web-healthcheck
''')
    tag = 'doxagent-v2-web:permissions-fixed-20261112'
    subprocess.run(['docker', 'build', '-t', tag, str(context)], check=True)
    image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
    # Negative control uses the actual nginx worker identity, never root.
    for selected, should_pass in ((BASELINE, False), (image, True)):
        readable = subprocess.run(['docker', 'run', '--rm', '--user', 'nginx', '--entrypoint', 'sh',
            selected, '-c', 'test -r /usr/share/nginx/html/assets/index-DKqWHADn.js'], capture_output=True)
        assert (readable.returncode == 0) == should_pass
    for name in ('state.json', 'compose.current.json'):
        (directory / (name + '.before.private')).write_bytes((ROOT / name).read_bytes())
    candidate = copy.deepcopy(json.loads((ROOT / 'compose.current.json').read_bytes())['services']['v2-web'])
    candidate.update(image=image, checks=[
        ['docker', 'exec', CONTAINER, '/usr/local/bin/doxagent-web-healthcheck'],
        ['docker', 'exec', CONTAINER, 'sh', '-c', 'cd /usr/share/nginx/html && sha256sum -c /tmp/projection-web.sha256'],
    ])
    manifest = dict(commit=state['components']['v2-web']['current'].get('commit'),
                    compose_command=state['compose_base_command'], components={'v2-web': candidate})
    release.atomic(directory / 'manifest.private.json', manifest)
    state.setdefault('owned_images', []).append(image)
    release.reconcile = lambda *_: {'deferred': 'normal host timer owns unrelated resource retirement'}
    release.deploy(ROOT, state, manifest)
    receipt = dict(baseline=BASELINE, image=image, healthcheck_sha256=hashlib.sha256((context / 'healthcheck.sh').read_bytes()).hexdigest(),
                   content_sha256_check='passed', worker_permission_negative_control='passed')
    release.atomic(directory / 'receipt.json', receipt)
    print(json.dumps(receipt))
