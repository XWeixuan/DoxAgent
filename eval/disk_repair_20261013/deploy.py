"""Deploy only the approved disk repair overlay against exact running baselines."""
import json
import subprocess
from pathlib import Path

ROOT = Path('/home/ubuntu/disk-repair-20261013')
EXPECTED = {'v2-message-bus': 'sha256:7d3612194b29db3d8a17dad1ac78aa00f28c1d26f557bb2db5b9067f84d9bf62',
            'v2-projector': 'sha256:92163da1783aa178ee33a108af19dec17ef3a60651033e4be2571af21da1e7a3'}


def run(args, timeout=180):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])
    return result.stdout


receipt = {'services': {}}
for service, expected in EXPECTED.items():
    name = 'doxagent-v2-' + service + '-1'
    before = json.loads(run(['docker', 'inspect', name]))[0]
    assert before['Image'] == expected, 'production baseline changed: ' + service
    files = before['Config']['Labels']['com.docker.compose.project.config_files'].split(',')
    image = 'doxagent-v2:disk-repair-' + ('bus' if service == 'v2-message-bus' else 'read') + '-20261013-r1'
    override = ROOT / (service + '.yaml')
    text = 'services:\n  ' + service + ':\n    image: ' + image + '\n'
    if service == 'v2-projector':
        text += ('  v2-maintenance:\n    profiles: !reset []\n    image: ' + image + '\n'
                 '    restart: unless-stopped\n')
    override.write_text(text)
    command = ['docker', 'compose', '-p', 'doxagent-v2', '--env-file', '/home/ubuntu/doxagent/.env.v2']
    for file in files + [str(override)]:
        assert Path(file).is_file()
        command += ['-f', file]
    run(command + ['config', '--quiet'])
    receipt['services'][service] = dict(before_image=before['Image'], before_id=before['Id'],
                                        before_files=files, override=str(override))
    (ROOT / 'deployment-receipt.json').write_text(json.dumps(receipt, indent=2))
    print('drain ' + service, flush=True)
    run(['docker', 'stop', '-t', '60', name], timeout=90)
    try:
        run(command + ['up', '-d', '--no-deps', '--no-build', service])
    except Exception:
        run(['docker', 'start', name])
        raise
    after = json.loads(run(['docker', 'inspect', name]))[0]
    receipt['services'][service].update(after_image=after['Image'], after_id=after['Id'])
    if service == 'v2-projector':
        run(command + ['up', '-d', '--no-deps', '--no-build', 'v2-maintenance'])
    (ROOT / 'deployment-receipt.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt), flush=True)
