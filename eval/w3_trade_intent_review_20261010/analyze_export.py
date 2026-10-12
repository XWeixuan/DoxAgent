"""Read local grouped files and report trade-evidence counts for review."""
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

files = sorted((Path(__file__).resolve().parent / 'export').glob('*.json'))
records = [record for file in files for record in json.loads(file.read_text(encoding='utf-8'))['records']]
assert all(len(json.loads(file.read_text(encoding='utf-8'))['records']) <= 5 for file in files)
assert len({record['intent_id'] for record in records}) == len(records)
by_ticker = defaultdict(list)
for record in records:
    by_ticker[record['ticker']].append(record)

def outcome(record):
    executions = record['trade']['executions']
    entry = [e for e in executions if e['entry']]
    exit_ = [e for e in executions if e['exit']]
    if entry:
        return 'ENTRY_AND_EXIT' if exit_ and all(e['lot_remaining_quantity'] == 0 for e in entry) else 'ENTRY_OPEN_OR_PARTIAL'
    return 'NO_ENTRY_FILL'

for ticker, rows in sorted(by_ticker.items()):
    settled = [e for r in rows for e in r['trade']['executions']
               if e['realized_pnl_usd'] is not None and
               all(not p['provisional'] for p in e['realized_pnl_records'])]
    print(json.dumps({'ticker': ticker, 'records': len(rows),
                      'intent_statuses': Counter(r['intent_status'] for r in rows),
                      'case_trade_states': Counter(r['case_summary']['trade']['state'] for r in rows),
                      'outcomes': Counter(outcome(r) for r in rows),
                      'known_final_realized_pnl_usd': str(sum((Decimal(e['realized_pnl_usd']) for e in settled), Decimal(0))),
                      'nonprovisional_pnl_lots': len(settled)}, ensure_ascii=False))

print('NO_ENTRY_REASONS', dict(Counter(
    e['execution']['entry_reason'] or ','.join(e['execution']['execution_reason_codes'])
    for r in records if outcome(r) == 'NO_ENTRY_FILL' for e in r['trade']['executions'])))
print('W3_ORIGINS', dict(Counter(r['decision_origin'] for r in records)))
print('W3_TRADE_TRUE', sum(r['w3_decision']['expert_trade']['trade'] is True for r in records))
print('MISSING_EXECUTION_PROJECTION', sum(not r['trade']['executions'] for r in records))
print('PENDING_REALIZED', [(r['ticker'],r['case_id'],e['realized_pnl_usd'],e['lot_remaining_quantity'])
                           for r in records for e in r['trade']['executions'] if e['entry'] and
                           (e['realized_pnl_usd'] is None or any(p['provisional'] for p in e['realized_pnl_records']))])
