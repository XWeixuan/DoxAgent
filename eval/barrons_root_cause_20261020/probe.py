"""Read-only production evidence; credentials and browser storage excluded."""
import json
import sqlite3
import subprocess
from datetime import UTC, datetime


def run(*args):
    return subprocess.check_output(args, text=True, timeout=45).strip()


def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not any(
            s in k.casefold() for s in ('token', 'password', 'cookie', 'secret', 'authorization'))}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


names = ['doxagent-v2-v2-site-access-1', 'doxagent-v2-v2-message-bus-1']
containers = json.loads(run('docker', 'inspect', *names))
paths = [next(m['Source'] for m in c['Mounts'] if m['Destination'] == dest)
         for c, dest in zip(containers, ['/site-data', '/data'])]
site = sqlite3.connect('file:' + paths[0] + '/registry/site_strategy.sqlite3?mode=ro', uri=True, timeout=3)
site.row_factory = sqlite3.Row
bus = sqlite3.connect('file:' + paths[1] + '/bus/bus.sqlite3?mode=ro', uri=True, timeout=3)
bus.row_factory = sqlite3.Row


def data(sql, args=(), db=site):
    return [json.loads(r[0]) for r in db.execute(sql, args)]


out = {'at': datetime.now(UTC).isoformat(), 'images': [c['Image'] for c in containers]}
out['scope'] = data("select data_json from source_access_scopes where scope_id='source:barrons_ticker_news'")
ids = [s[k] for s in out['scope'] for k in ('primary_identity_id', 'fallback_identity_id') if s.get(k)]
args = (json.dumps(ids),)
out['identities'] = data('select r.spec_json from browser_identity_revisions r join browser_identity_heads h on h.identity_id=r.identity_id and h.active_revision=r.revision where r.identity_id in (select value from json_each(?))', args)
out['strategy'] = data("select r.spec_json from site_strategy_revisions r join site_strategy_heads h on h.site_id=r.site_id and h.active_revision=r.revision where r.site_id='barrons'")
for table in ('browser_profiles', 'identity_runtime', 'maintenance_sessions'):
    key = 'profile_id' if table == 'browser_profiles' else 'identity_id'
    values = [i['profile_id'] for i in out['identities']] if key == 'profile_id' else ids
    out[table] = data(f'select data_json from {table} where {key} in (select value from json_each(?))', (json.dumps(values),))
out['runtime'] = data("select data_json from site_runtime where runtime_key='scope:source:barrons_ticker_news'")
out['auth'] = data("select data_json from site_identity_auth where site_id='barrons'")
out['tasks'] = data("select data_json from challenge_tasks where json_extract(data_json,'$.site_id')='barrons' order by updated_at desc limit 20")
out['events'] = [dict(r) for r in site.execute("select operation_id,occurred_at,combination_id,category,payload_json from access_events where site_id='barrons' and occurred_at >= '2026-10-17' order by occurred_at desc limit 800")]
out['history'] = [dict(r) for r in site.execute("select category,min(occurred_at) first_at,max(occurred_at) last_at,count(*) count from access_events where site_id='barrons' group by category")]
out['risk_history'] = [dict(r) for r in site.execute("select operation_id,occurred_at,category,payload_json from access_events where site_id='barrons' and category in ('COMBINATION_RISK_FAILURE','PROFILE_PROBE') order by occurred_at desc limit 12")]
out['polls'] = data("select data_json from poll_states where source_id='barrons_ticker_news'", db=bus)
out['failures'] = data("select data_json from acquisition_failures where source_id='barrons_ticker_news' order by last_seen_at desc limit 25", db=bus)
out['egresses'] = data('select data_json from proxy_egresses')
out['regions'] = [dict(r) for r in site.execute('select * from egress_regions')]
print(json.dumps(public(out)))
