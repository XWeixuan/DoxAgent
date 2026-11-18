"""Bounded, read-only production release acceptance (no secret configuration)."""
import datetime
import json
import pathlib
import shutil
import sqlite3
import subprocess
import time

root = pathlib.Path('/var/lib/doxagent/releases')
data = pathlib.Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
manifest = json.loads((root / 'storage-source-release-20261118/final.json').read_text())
state = json.loads((root / 'state.json').read_text())
ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True).split()
containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True))
by_role = {(c['Config'].get('Labels') or {}).get('com.docker.compose.service'): c for c in containers}
result = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), commit=manifest['commit'],
              available_bytes=shutil.disk_usage('/').free, components={}, running=sum(c['State']['Running'] for c in containers))
for role, candidate in manifest['components'].items():
    c = by_role[role]
    env = dict(e.split('=', 1) for e in c['Config'].get('Env', []) if '=' in e)
    release = state['components'][role]
    item = dict(running=c['State']['Running'], health=c['State'].get('Health', {}).get('Status'),
                image=c['Image'], expected_image=candidate['image'], commit=env.get('DOXAGENT_BUILD_COMMIT'),
                published_refs=env.get('DOXAGENT_PUBLISHED_CONTENT_REFS'), single_file=env.get('DOXAGENT_CONTENT_SINGLE_FILE'),
                rollback_commit=release.get('previous', {}).get('commit'),
                restarts=c['RestartCount'], started_at=c['State']['StartedAt'])
    item['ok'] = (item['running'] and item['health'] not in ('unhealthy', 'starting') and item['image'] == item['expected_image']
                  and item['commit'] == candidate['environment']['DOXAGENT_BUILD_COMMIT'] and item['published_refs'] == item['single_file'] == '1'
                  and item['rollback_commit'].startswith(('b1aaddaa2','1b0494dae')))
    result['components'][role] = item
for name, relative in [('read', 'read/v2.sqlite3'), ('acquisition', 'bus/acquisition.sqlite3')]:
    p = (data / relative).resolve()
    with sqlite3.connect(p.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        if name == 'read':
            result['sources'] = db.execute('SELECT source,head,checkpoint,error,checked_at FROM source_health ORDER BY source').fetchall()
            result['gaps'] = db.execute('SELECT source,count(*) FROM gaps GROUP BY source').fetchall()
        else:
            result['pending_captures'] = db.execute("SELECT count(*) FROM captures WHERE status='PENDING'").fetchone()[0]
            result['recent_captures'] = db.execute('SELECT observed_at,status,accepted_at FROM captures ORDER BY observed_at DESC LIMIT 5').fetchall()
            result['collector_configuration'] = json.loads(db.execute("SELECT value FROM meta WHERE key='configuration'").fetchone()[0])
result['ok'] = all(x['ok'] for x in result['components'].values())
result['http_probes'] = {}
for role, port, path in [('v2-api', 8000, '/readyz'), ('codex-worker', 8791, '/healthz'), ('v2-site-access', 8011, '/readyz')]:
    code = ("import urllib.request,json; r=urllib.request.urlopen('http://127.0.0.1:" + str(port) + path + "',timeout=8); "
            "d=json.load(r); print(json.dumps({'http':r.status,'ok':d.get('ok'),'status':d.get('status')}))")
    result['http_probes'][role] = json.loads(subprocess.check_output(['docker','exec',by_role[role]['Id'],'python','-c',code],text=True))
print(json.dumps(result))
