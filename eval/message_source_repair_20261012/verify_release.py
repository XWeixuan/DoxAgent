"""Read-only exact deployed-file comparison against staged overlay and old image."""
import hashlib
import json
import pathlib
import subprocess

root = pathlib.Path('/home/ubuntu/source-repair-20261012')
release = json.loads((root / 'release.json').read_text())
checks = []
for suffix, dockerfile in [('site-access', 'site'), ('message-bus', 'bus'), ('content-enrichment', 'enrich')]:
    container = 'doxagent-v2-v2-' + suffix + '-1'
    old_image = release['before'][container]['image']
    for line in (root / ('Dockerfile.' + dockerfile)).read_text().splitlines():
        if not line.startswith('COPY '):
            continue
        _, source, destination = line.split()
        expected = hashlib.sha256((root / source).read_bytes()).hexdigest()
        current = subprocess.check_output(['docker', 'exec', container, 'sha256sum', destination], text=True).split()[0]
        previous = subprocess.check_output(['docker', 'run', '--rm', '--network=none', '--entrypoint=sha256sum', old_image, destination], text=True).split()[0]
        checks.append(dict(container=container, path=destination, before_sha256=previous, after_sha256=current, staged_sha256=expected, match=current == expected))
print(json.dumps({'release': release, 'files': checks, 'all_match': all(r['match'] for r in checks)}))
