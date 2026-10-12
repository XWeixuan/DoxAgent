import json
import sqlite3
from datetime import datetime, timedelta, timezone

ROOT = '/var/lib/docker/volumes/doxagent-v2_v2-data/_data'
for label, path in [('read', ROOT + '/read/v2.sqlite3'), ('runtime', ROOT + '/runtime/runtime.sqlite3')]:
    db = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    names = ['objects', 'object_current', 'runtime_v2_cases', 'runtime_values',
             'te_executions', 'te_fills', 'te_lots', 'te_allocations']
    out = {'database': label, 'tables': {}}
    for name in names:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
            continue
        cols = [row['name'] for row in db.execute('PRAGMA table_info(' + name + ')')]
        out['tables'][name] = cols
    if label == 'read':
        out['kinds'] = [dict(row) for row in db.execute(
            "SELECT kind, count(*) AS n FROM object_current WHERE kind IN ('case','policy_hit_observation','execution','realized_pnl') GROUP BY kind")]
        out['recent_hits'] = [dict(row) for row in db.execute(
            "SELECT ticker,count(*) AS n FROM object_current WHERE kind='policy_hit_observation' AND json_extract(payload,'$.hit')=1 GROUP BY ticker")]
        since = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
        out['since_utc'] = since
        out['true_hit_rows'] = [dict(row) for row in db.execute(
            "SELECT ticker,id,parent,sort_key,day,payload FROM object_current WHERE kind='policy_hit_observation' AND json_extract(payload,'$.hit')=1 ORDER BY ticker,id")]
        out['case_hit_counts'] = [dict(row) for row in db.execute(
            "SELECT ticker,count(*) AS n FROM object_current WHERE kind='case' AND json_extract(payload,'$.final_policy_hit.value')=1 GROUP BY ticker")]
        out['case_example'] = [dict(row) for row in db.execute(
            "SELECT ticker,id,sort_key,day,payload FROM object_current WHERE kind='case' AND json_extract(payload,'$.final_policy_hit.value')=1 LIMIT 1")]
        out['execution_example'] = [dict(row) for row in db.execute(
            "SELECT ticker,id,parent,payload FROM object_current WHERE kind='execution' LIMIT 1")]
        out['pnl_example'] = [dict(row) for row in db.execute(
            "SELECT ticker,id,parent,payload FROM object_current WHERE kind='realized_pnl' LIMIT 1")]
        cid = 'case_c35b297c6dec48f88f44c00f96128821'
        out['case_related'] = []
        for row in db.execute("SELECT kind,ticker,id,parent,payload FROM object_current WHERE ticker='BE' AND (id=? OR parent=?)", (cid,cid)):
            payload = json.loads(row['payload'])
            out['case_related'].append({'kind':row['kind'],'id':row['id'],'parent':row['parent'],
                                        'keys':list(payload), 'sample':payload if row['kind'] in ('case','policy_hit_observation') else None})
    else:
        out['namespaces'] = [dict(row) for row in db.execute(
            "SELECT namespace,count(*) AS n FROM runtime_values WHERE namespace IN ('trade_intents','candidates') GROUP BY namespace")]
        for table in ['te_fills', 'te_lots', 'te_allocations']:
            out[table + '_example'] = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' LIMIT 1')]
        out['runtime_case_keys'] = [list(json.loads(row['payload_json'])) for row in db.execute(
            "SELECT payload_json FROM runtime_v2_cases WHERE case_id='case_c35b297c6dec48f88f44c00f96128821'")]
    db.rollback()
    print(json.dumps(out, ensure_ascii=False))
