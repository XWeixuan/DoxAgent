import json
import sqlite3
from datetime import datetime, timezone
from doxagent.v2_read.native_content import NativeContent

db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
content = NativeContent('/data/runtime/runtime.sqlite3')
ids = [json.loads(row[0])['intent']['case_id'] for row in db.execute('SELECT payload FROM te_executions')]
cases = []
for identity in ids:
    row = db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?', (identity,)).fetchone()
    if not row:
        continue
    case = content.decode(json.loads(row[0]))
    cases.append({key: case.get(key) for key in ('case_id','ticker','created_at','updated_at','trading_date','status','version_pin')} | {
        'provisional': case.get('frozen_inputs', {}).get('provisional', []),
        'source_message_id': case.get('source', {}).get('source_message_id'),
    })
result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'cases': cases}
db.rollback()
read = sqlite3.connect('file:/data/read/v2.sqlite3?mode=ro', uri=True)
read.execute('PRAGMA query_only=ON')
read.execute('BEGIN')
result['realized_pnl'] = [{'ticker': row[0], 'id': row[1], 'parent': row[2], 'payload': json.loads(row[3])}
    for row in read.execute("SELECT ticker,id,parent,payload FROM objects WHERE kind='realized_pnl' AND valid_to IS NULL")]
result['execution_summaries'] = [{'ticker': row[0], 'id': row[1], 'payload': json.loads(row[2])}
    for row in read.execute("SELECT ticker,id,payload FROM objects WHERE kind='execution' AND valid_to IS NULL")]
read.rollback()
print(json.dumps(result, ensure_ascii=False))
