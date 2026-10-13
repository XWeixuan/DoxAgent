"""Record actual deployed image identities and staged-file equality."""
import hashlib
import json
import pathlib
import subprocess
from datetime import UTC, datetime

root = pathlib.Path('/home/ubuntu/source-repair-20261014')
receipt = json.loads((root / 'release.json').read_text())
receipt['accepted_at'] = datetime.now(UTC).isoformat()
for suffix in receipt['services']:
    container = 'doxagent-v2-v2-' + suffix + '-1'
    row = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]
    receipt['after'][suffix] = {
        'container_id': row['Id'], 'image': row['Image'], 'tag': row['Config']['Image'],
        'started': row['State']['StartedAt'], 'status': row['State']['Status'],
        'health': row['State'].get('Health', {}).get('Status'),
    }
receipt['files'] = []
for suffix in ('site-access', 'message-bus', 'api'):
    for path in (root / 'staging' / suffix).rglob('*.py'):
        relative = path.relative_to(root / 'staging' / suffix)
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        remote_path = '/app/src/doxagent/' + str(relative)
        actual = subprocess.check_output(['docker', 'exec', 'doxagent-v2-v2-'+suffix+'-1',
            'sha256sum', remote_path], text=True).split()[0]
        receipt['files'].append({'service': suffix, 'path': str(relative),
            'sha256': actual, 'matches_staging': expected == actual})
        assert expected == actual
for suffix in ('chrome-supervisor', 'content-enrichment', 'projector'):
    assert receipt['after'][suffix]['container_id'] == receipt['services'][suffix]['container_id']
receipt['providers'] = json.loads(pathlib.Path(
    '/opt/doxagent-egress-clash/providers/status.json').read_text())
receipt['providers'] = {name: {key: value for key, value in row.items()
    if key not in ('usage', 'endpoint_fingerprints')} for name, row in receipt['providers'].items()}
(root / 'release-final.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps({'files_verified': len(receipt['files']), 'unrelated_containers_unchanged': True}))
