"""Run on the release host as root; stdout is a small, secret-free receipt."""
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path('/var/lib/doxagent/releases')
OUT = Path('/var/lib/doxagent/bus-collection-20261026')
OUT.mkdir(mode=0o700, exist_ok=True)
DATA = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
SITE = Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')


def save(path, value):
    with path.open('w') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, indent=2)


def rows(path, sql):
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql)]


def snapshot(name):
    ids = subprocess.check_output(['docker', 'ps', '-aq']).decode().split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids]))
    roles = {}
    for c in containers:
        role = c['Config'].get('Labels', {}).get('com.docker.compose.service')
        if not role:
            continue
        env = dict(x.split('=', 1) for x in c['Config'].get('Env', []))
        roles[role] = {
            'id': c['Id'], 'image': c['Image'], 'state': c['State']['Status'],
            'health': c['State'].get('Health', {}).get('Status'), 'restarts': c['RestartCount'],
            'started_at': c['State']['StartedAt'],
            'commit': c['Config']['Labels'].get('org.opencontainers.image.revision'),
            'nano_cpus': c['HostConfig'].get('NanoCpus'),
            'cpu_shares': c['HostConfig'].get('CpuShares'),
        }
        if role == 'v2-site-access':
            roles[role]['limits'] = {k: env.get(k) for k in (
                'DOXAGENT_SITE_ACCESS_BROWSER_MAX_PAGES', 'DOXAGENT_SITE_ACCESS_BODY_BROWSER_MAX_PAGES',
                'DOXAGENT_SITE_ACCESS_BODY_BROWSER_BORROW')}
    mem = {l.split(':')[0]: l.split(':')[1].strip() for l in Path('/proc/meminfo').read_text().splitlines()
           if l.split(':')[0] in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')}
    protected = rows(SITE, "SELECT allocation_id,target_id,owner_kind FROM owned_browser_targets WHERE owner_kind!='BUSINESS' AND state NOT IN ('CLOSED','LOST')")
    identities = rows(SITE, 'SELECT identity_id,active_revision FROM browser_identity_heads ORDER BY identity_id')
    sites = rows(SITE, 'SELECT site_id,active_revision FROM site_strategy_heads ORDER BY site_id')
    config = rows(DATA/'bus/bus.sqlite3', 'SELECT data_json FROM source_definitions ORDER BY source_id')
    config += rows(DATA/'bus/bus.sqlite3', 'SELECT data_json FROM ticker_source_bindings ORDER BY binding_id')
    report = {
        'at': datetime.now(UTC).isoformat(), 'cpus': os.cpu_count(), 'memory': mem,
        'cpu_pressure': Path('/proc/pressure/cpu').read_text(), 'roles': roles,
        'protected_targets': protected, 'identity_revisions': identities, 'site_revisions': sites,
        'bus_configuration_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        'captures': rows(DATA/'bus/bus.sqlite3', 'SELECT status,count(*) AS count,sum(message_count-intake_position) AS remaining FROM poll_captures GROUP BY status'),
        'ir': rows(DATA/'bus/bus.sqlite3', "SELECT endpoint_id,json_extract(data_json,'$.status') AS status,json_extract(data_json,'$.last_success_at') AS last_success_at FROM ir_endpoint_states"),
        'bus_head': rows(DATA/'bus/bus.sqlite3', "SELECT head FROM v2_source_state WHERE source='bus'"),
        'read_checkpoints': rows(DATA/'read/v2.sqlite3', 'SELECT source,position FROM checkpoints'),
    }
    save(OUT/(name+'.json'), report)
    print(json.dumps({'receipt': str(OUT/(name+'.json')), 'cpus': report['cpus'], 'memory': mem}))


def manifest(commit):
    state = json.loads((ROOT/'state.json').read_text())
    build = json.loads((ROOT/(commit+'.build.json')).read_text())
    base = state.get('compose_base_command')
    if not base:
        raise ValueError('missing standard Compose base')
    result = {'compose_command': base, 'commit': commit, 'components': {}}
    for role, kind in [('v2-site-access', 'site'), ('v2-message-bus', 'backend'),
                       ('v2-content-enrichment', 'backend'), ('v2-scheduler', 'backend')]:
        current = state['components'][role]['current']
        candidate = copy.deepcopy(current.get('compose', {}))
        candidate['image'] = build['images'][kind]['image']
        candidate['schema'] = current.get('schema')
        if role == 'v2-site-access':
            candidate.setdefault('environment', {})['DOXAGENT_SITE_ACCESS_BROWSER_MAX_PAGES'] = '8'
        result['components'][role] = candidate
    save(OUT/'release.private.json', result)
    print(json.dumps({'components': list(result['components']), 'commit': commit}))


if sys.argv[1] == 'before':
    for file in ('state.json', 'compose.current.json'):
        shutil.copy2(ROOT/file, OUT/('before-'+file))
    snapshot('before')
elif sys.argv[1] == 'manifest':
    manifest(sys.argv[2])
else:
    snapshot(sys.argv[1])
