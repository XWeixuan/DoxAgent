"""Read-only maintenance evidence; excludes tokens, cookies and account fields."""
import json
import os
import sqlite3
import subprocess


def run(*args):
    return subprocess.check_output(args, text=True).strip()


containers = json.loads(run('docker', 'inspect', 'doxagent-v2-v2-site-access-1',
                           'doxagent-v2-v2-chrome-supervisor-1'))
print('containers', json.dumps([{'name': c['Name'], 'image': c['Config']['Image'],
      'id': c['Id'], 'ns': os.readlink(f"/proc/{c['State']['Pid']}/ns/net")} for c in containers]))
volume = next(m['Source'] for m in containers[0]['Mounts'] if m['Destination'] == '/site-data')
db = sqlite3.connect(f'file:{volume}/registry/site_strategy.sqlite3?mode=ro', uri=True)
for table in ('maintenance_operations', 'maintenance_sessions', 'challenge_tasks'):
    rows = [json.loads(r[0]) for r in db.execute(f'SELECT data_json FROM {table} ORDER BY updated_at DESC LIMIT 100')]
    if table == 'maintenance_operations':
        rows = rows[:30]
    else:
        rows = [r for r in rows if r.get('state') not in ('CLOSED', 'RESOLVED') and not r.get('superseded_by')]
    for row in rows:
        row.pop('control_token', None)
    print(table, json.dumps(rows, ensure_ascii=True))
print('indexes', json.dumps(db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='maintenance_sessions'").fetchall()))
print('reuters_scope', db.execute("SELECT data_json FROM source_access_scopes WHERE scope_id='source:reuters_site_search'").fetchone()[0])
print('reuters_auth', json.dumps(json.loads(db.execute("SELECT r.spec_json FROM site_strategy_heads h JOIN site_strategy_revisions r ON h.site_id=r.site_id AND h.active_revision=r.revision WHERE h.site_id='reuters'").fetchone()[0])['auth']))
