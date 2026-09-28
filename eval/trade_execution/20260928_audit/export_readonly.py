import json
import sqlite3
from datetime import datetime, timezone

db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.row_factory = sqlite3.Row
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'tables': {}}
for table in ('te_profiles', 'te_executions', 'te_jobs', 'te_attempts', 'te_events', 'te_fills',
              'te_lots', 'te_allocations', 'te_lot_adjustments', 'te_sync_state'):
    result['tables'][table] = [dict(row) for row in db.execute('SELECT * FROM ' + table)]
result['runtime_values'] = [dict(row) for row in db.execute(
    "SELECT * FROM runtime_values WHERE namespace IN ('trade_intents','trade_receipts',"
    "'execution_fees','execution_gaps','execution_event_gaps','execution_position_baselines',"
    "'execution_connections','trade_execution')")]
db.rollback()
def redact(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED_ACCOUNT]' if k in ('account', 'expected_account_id') else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        try:
            return json.dumps(redact(json.loads(value)), ensure_ascii=False)
        except (ValueError, TypeError):
            return value
    return value
accounts = {row['account'] for row in result['tables']['te_profiles']}
output = json.dumps(redact(result), ensure_ascii=False)
for account in accounts:
    output = output.replace(account, '[REDACTED_ACCOUNT]')
print(output)
