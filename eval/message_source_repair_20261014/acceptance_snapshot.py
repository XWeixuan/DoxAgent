"""Read-only current source receipt; aggregate, do not infer long-run recovery."""
import json
import sqlite3
from datetime import UTC, datetime

db = sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro', uri=True, timeout=5)
states = [json.loads(row[0]) for row in db.execute('select data_json from poll_states')]
sources = {}
for row in states:
    source = sources.setdefault(row['source_id'], [])
    source.append({key: row.get(key) for key in (
        'binding_id', 'status', 'last_attempt_at', 'last_success_at',
        'last_error_code', 'last_error_message', 'consecutive_failures', 'next_dispatch_at')})
print(json.dumps({'at': datetime.now(UTC).isoformat(), 'sources': sources,
    'tables': [r[0] for r in db.execute("select name from sqlite_master where type='table'")]}))
