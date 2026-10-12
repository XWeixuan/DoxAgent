"""Incident evidence only: read-only SQLite and existing GET diagnostics."""

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime


def connect(path):
    db = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


out = {'observed_at_utc': datetime.now(UTC).isoformat(), 'mode': sys.argv[1]}
if sys.argv[1] == 'bus':
    db = connect('/data/bus/bus.sqlite3')
    out['poll_states'] = [json.loads(r[0]) for r in db.execute('select data_json from poll_states')]
    out['distribution_runs_24h'] = rows(db, "select source_id,status,count(*) n from distribution_runs where created_at >= '2026-10-08T11:00:00' group by 1,2")
    out['outbox_schema'] = rows(db, "select sql from sqlite_master where name='site_strategy_result_outbox'")
    out['outbox_sample'] = rows(db, 'select * from site_strategy_result_outbox limit 1')
    out['files'] = {p: os.stat(p).st_size for p in ['/data/bus/bus.sqlite3', '/data/bus/bus.sqlite3-wal'] if os.path.exists(p)}
else:
    import httpx
    from doxagent.settings import DoxAgentSettings
    from doxagent.site_strategy.tokens import read_token

    settings = DoxAgentSettings()
    token = read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
    sites = ['barrons', 'digitimes', 'investorshub', 'investing', 'reuters', 'trendforce', 'globenewswire']
    with httpx.Client(base_url='http://127.0.0.1:8011', headers={'Authorization': 'Bearer ' + token}, timeout=20) as c:
        out['readiness'] = c.get('/readyz').json()
        out['diagnostics'] = c.get('/v1/diagnostics/access').json()
        out['strategies'] = {s: c.get('/v1/sites/' + s).json() for s in sites}
        out['health'] = {s: c.get('/v1/combinations/' + s).json() for s in sites}
        out['egresses'] = c.get('/v1/egresses').json()
    db = connect('/site-data/registry/site_strategy.sqlite3')
    out['event_range'] = rows(db, 'select min(occurred_at) first,max(occurred_at) last,count(*) n from access_events')
    out['recent_access'] = {}
    for site in sites:
        out['recent_access'][site] = rows(db, 'select occurred_at,category,combination_id,payload_json from access_events where site_id=? order by occurred_at desc limit 4', (site,))
    out['investing_onset'] = rows(db, "select occurred_at,category,combination_id,payload_json from access_events where site_id='investing' and occurred_at between '2026-10-06T14:30' and '2026-10-06T14:46' and category in ('COMBINATION_RISK_FAILURE','COMBINATION_RECOVERY_PROBE') order by occurred_at")
    out['body_outcomes_24h'] = rows(db, "select final_site_id,outcome,reason,count(*) n,max(completed_at) last from body_outcomes where completed_at>='2026-10-08T11:00:00' group by 1,2,3")
    out['database_pragmas'] = {q: db.execute('pragma ' + q).fetchone()[0] for q in ['page_count', 'page_size', 'freelist_count', 'max_page_count', 'journal_mode']}
    out['database_sizes'] = {p: os.stat(p).st_size for p in ['/site-data/registry/site_strategy.sqlite3', '/site-data/registry/site_strategy.sqlite3-wal'] if os.path.exists(p)}
    stat = os.statvfs('/site-data')
    out['filesystem'] = {'total_bytes': stat.f_blocks * stat.f_frsize, 'available_bytes': stat.f_bavail * stat.f_frsize, 'free_including_reserved_bytes': stat.f_bfree * stat.f_frsize}
db.close()
print(json.dumps(out, ensure_ascii=False, indent=2))
