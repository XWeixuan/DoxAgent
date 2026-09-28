"""Recompute totals and an intent-level CSV from the immutable redacted export."""
import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

root = Path(__file__).parent
source = json.loads((root / 'ledger_redacted.json').read_text(encoding='utf-8-sig'))
tables = source['tables']
unpack = lambda row: json.loads(row['payload'])
attempts = {row['id']: unpack(row) for row in tables['te_attempts']}
jobs = {row['id']: unpack(row) for row in tables['te_jobs']}
fees = {unpack(row)['exec_id']: unpack(row) for row in source['runtime_values'] if row['namespace'] == 'execution_fees'}
effective = {}
for row in tables['te_fills']:
    key = (row['account'], row['family'])
    if key not in effective or row['correction'] > effective[key]['correction']:
        effective[key] = row
fills = [unpack(row) for row in effective.values()]
records = []
for row in sorted(tables['te_executions'], key=lambda item: unpack(item)['created_at']):
    execution = unpack(row)
    identity = execution['id']
    intent = execution['intent']
    owned = [fill for fill in fills if jobs[attempts[fill['attempt_id']]['job_id']]['execution_id'] == identity]
    actual = [attempt for attempt in attempts.values() if jobs[attempt['job_id']]['execution_id'] == identity]
    entries = [fill for fill in owned if jobs[attempts[fill['attempt_id']]['job_id']]['leg'] == 'ENTRY']
    gross = sum((Decimal(fill['quantity']) * Decimal(fill['price']) * (1 if fill['side'] == 'SELL' else -1) for fill in owned), Decimal(0))
    commission = sum((Decimal(fees[fill['exec_id']]['commission']) for fill in owned), Decimal(0))
    codes = sorted({attempt['broker_error']['code'] for attempt in actual if attempt.get('broker_error')})
    records.append({'execution_id': identity, 'ticker': execution['ticker'], 'released_et': datetime.fromisoformat(intent['released_at']).astimezone(ZoneInfo('America/New_York')).isoformat(), 'direction': intent['trade']['decision'], 'origin': intent['trade']['decision_origin'], 'entry_result': execution.get('entry_result'), 'entry_reason': execution.get('entry_reason'), 'entry_order_count': len([attempt for attempt in actual if jobs[attempt['job_id']]['leg'] == 'ENTRY']), 'entry_fill_count': len(entries), 'entry_quantity': str(sum((Decimal(fill['quantity']) for fill in entries), Decimal(0))), 'entry_notional_usd': str(sum((Decimal(fill['quantity']) * Decimal(fill['price']) for fill in entries), Decimal(0))), 'closed_net_pnl_usd': str(gross-commission) if owned else '', 'broker_error_codes': ','.join(map(str,codes)), 'title': intent['trade']['source']['snapshot'].get('title')})
with (root / 'intent_summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
summary = {'captured_at': source['captured_at'], 'intent_count': len(records), 'entry_results': dict(Counter(row['entry_result'] for row in records)), 'entry_reasons': dict(Counter(row['entry_reason'] for row in records)), 'effective_fill_count': len(fills), 'closed_net_pnl_usd': str(sum((Decimal(row['closed_net_pnl_usd']) for row in records if row['closed_net_pnl_usd']), Decimal(0))), 'open_owned_lots': [unpack(row) for row in tables['te_lots'] if unpack(row)['remaining_qty'] != 0]}
(root / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
