"""Prepare two existing-owner release manifests; private configs stay on host."""
import copy
import json
import os
import shutil
from pathlib import Path

root = Path('/var/lib/doxagent/releases')
audit = root / 'storage-source-release-20261118'
audit.mkdir(exist_ok=True, mode=0o700)
os.chmod(audit, 0o700)
for name in ('state.json', 'compose.current.json'):
    target = audit / ('before-' + name)
    if not target.exists():
        shutil.copy2(root / name, target)
        os.chmod(target, 0o600)
state = json.loads((root / 'state.json').read_text())
roles = [
    'v2-projector', 'v2-api', 'v2-control', 'v2-delivery',
    'v2-initialization', 'v2-cdecr-executor', 'v2-o4', 'v2-executor',
    'v2-site-access', 'initialization-guardian', 'codex-worker',
    'v2-scheduler', 'v2-message-collector', 'v2-message-bus',
    'v2-content-enrichment', 'v2-maintenance', 'v2-transport-maintenance',
]
for prefix, enabled, phase in [('b1aaddaa2', '0', 'compatible'), ('1b0494dae', '1', 'final')]:
    matches = list(root.glob(prefix + '*.build.json'))
    assert len(matches) == 1, matches
    built = json.loads(matches[0].read_text())
    manifest = dict(compose_command=state['compose_base_command'], commit=built['commit'], components={})
    for role in roles:
        current = state['components'][role]['current']
        candidate = copy.deepcopy(current.get('compose', {}))
        kind = {'v2-site-access': 'site', 'initialization-guardian': 'guardian'}.get(role, 'backend')
        candidate['image'] = built['images'][kind]['image']
        candidate['schema'] = current.get('schema')
        candidate['health_seconds'] = 180
        env = candidate.setdefault('environment', {})
        env.update(DOXAGENT_BUILD_COMMIT=built['commit'], DOXAGENT_SOURCE_SHA256=built['source_archive_sha256'],
                   DOXAGENT_PUBLISHED_CONTENT_REFS=enabled, DOXAGENT_CONTENT_SINGLE_FILE=enabled)
        if kind == 'guardian':
            env['DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE'] = built['images']['repair-agent']['tag']
        manifest['components'][role] = candidate
    path = audit / (phase + '.json')
    path.write_text(json.dumps(manifest))
    os.chmod(path, 0o600)
print(json.dumps({'roles': roles, 'phases': ['compatible', 'final']}))
