"""Read-only W3-derived formal trade-intent export; never writes remotely."""
import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
READ = ROOT / 'read'
RUNTIME = ROOT / 'runtime'
as_of = datetime.now(timezone.utc)
since = as_of - timedelta(days=21)

def iso(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

def open_ro(path):
    db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db

rd = open_ro(READ / 'v2.sqlite3')
rt = open_ro(RUNTIME / 'runtime.sqlite3')

def file_content(base, sha):
    assert len(sha) == 64 and all(c in '0123456789abcdef' for c in sha)
    path = base / sha[:2] / sha
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    raw = b''.join(gzip.decompress((path / str(i)).read_bytes()) for i in range(len(manifest['chunks'])))
    assert len(raw) == manifest['size'] and hashlib.sha256(raw).hexdigest() == sha
    return raw

def decode(value):
    if isinstance(value, dict) and '$doxagent_content_v1' in value:
        return decode(json.loads(file_content(READ / 'native-files', value['$doxagent_content_v1'])))
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value

def current(kind, ticker, identity):
    row = rd.execute('SELECT payload FROM object_current WHERE kind=? AND ticker=? AND id=?',
                     (kind, ticker, identity)).fetchone()
    return decode(json.loads(row['payload'])) if row else None

def children(kind, ticker, parent):
    return [decode(json.loads(r['payload'])) for r in rd.execute(
        'SELECT payload FROM object_current WHERE kind=? AND ticker=? AND parent=? ORDER BY id',
        (kind, ticker, parent))]

def content_text(ticker, ref):
    row = rd.execute('SELECT sha256,size,body FROM contents WHERE id=? AND ticker=?',
                     (ref['content_id'], ticker)).fetchone()
    if row is None or row['sha256'] != ref['sha256'] or row['size'] != ref['size_bytes']:
        raise ValueError('message content identity mismatch')
    location = rd.execute('SELECT sha256 FROM content_locations WHERE id=?', (ref['content_id'],)).fetchone()
    raw = file_content(READ / 'content-files', location['sha256']) if location else bytes(row['body'])
    if len(raw) != ref['size_bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('message content hash mismatch')
    return raw.decode('utf-8')

# Runtime records are few; preload the case links without copying frozen source bodies into output.
runtime_values = {}
selected_intents = {}
adjacent_non_intents = {}
for row in rt.execute("SELECT namespace,payload FROM runtime_values WHERE namespace IN "
                      "('trade_intents','analysis_trade_decisions','candidates')"):
    value = json.loads(row['payload'])
    case_id = value.get('case_id') or (value.get('trade') or {}).get('case_id')
    if case_id:
        runtime_values.setdefault(case_id, {}).setdefault(row['namespace'], []).append(value)
    trade = value.get('trade') or {}
    if not trade.get('w3_result'):
        continue
    at = value.get('released_at') or value.get('recorded_at') or value.get('created_at')
    if not at or not since <= iso(at) <= as_of:
        continue
    if row['namespace'] != 'trade_intents':
        key = row['namespace'] + ':' + str(value.get('trade_disposition') or value.get('status'))
        adjacent_non_intents[key] = adjacent_non_intents.get(key, 0) + 1
        continue
    if trade.get('decision_origin') != 'W3' and not (trade.get('w3_result') or {}).get('policy', {}).get('policy_ids'):
        raise ValueError('W3-linked intent has unknown origin: ' + value['intent_id'])
    if trade.get('w3_case_id') and trade['w3_case_id'] != trade['w3_result'].get('w3_case_id'):
        raise ValueError('W3 case identity mismatch: ' + value['intent_id'])
    identity = value['intent_id']
    if identity in selected_intents:
        raise ValueError('duplicate intent ID: ' + identity)
    selected_intents[identity] = value

def trade_info(ticker, case_id):
    executions = children('execution', ticker, case_id)
    intentions = []
    for item in runtime_values.get(case_id, {}).get('trade_intents', []):
        trade = item.get('trade') or {}
        intentions.append({k: item.get(k) for k in ('intent_id','status','released_at','selection_id','submitted',
                                                     'release_semantic_day','delivery_failure')})
        intentions[-1].update({'direction': trade.get('decision'),
                               'decision_origin': trade.get('decision_origin'),
                               'w3_case_id': trade.get('w3_case_id'),
                               'executed_policy_id': trade.get('executed_policy_id'),
                               'candidate_policy_ids': trade.get('candidate_policy_ids'),
                               'matched_condition_ids': trade.get('matched_condition_ids')})
    candidates = []
    for item in runtime_values.get(case_id, {}).get('candidates', []):
        candidates.append({k: item.get(k) for k in ('candidate_id','status','created_at','closed_cycle_id',
                                                      'origin_trade_eligible','trade_disposition')})
        candidates[-1]['trade'] = {k: (item.get('trade') or {}).get(k) for k in
                                   ('decision','executed_policy_id','candidate_policy_ids','matched_condition_ids')}
    details = []
    for ex in executions:
        eid = ex['execution_id']
        allocation_rows = list(rt.execute('SELECT fill_id,qty,kind FROM te_allocations WHERE lot_id=?', (eid,)))
        fills = []
        for allocation in allocation_rows:
            fill_row = rt.execute('SELECT payload FROM te_fills WHERE exec_id=?', (allocation['fill_id'],)).fetchone()
            if not fill_row:
                raise ValueError('allocation fill missing: ' + eid)
            fill = json.loads(fill_row['payload'])
            fills.append({'kind': allocation['kind'], 'side': fill['side'],
                          'quantity': allocation['qty'], 'price_usd': fill['price'],
                          'filled_at': fill['time'], 'broker_fill_id': fill['exec_id']})
        def leg(kind):
            values = [f for f in fills if f['kind'] == kind]
            qty = sum((Decimal(f['quantity']) for f in values), Decimal(0))
            if not qty:
                return None
            return {'side': values[0]['side'], 'quantity': str(qty),
                    'average_price_usd': str(sum(Decimal(f['quantity']) * Decimal(f['price_usd'])
                                                 for f in values) / qty),
                    'first_fill_at': min(f['filled_at'] for f in values),
                    'last_fill_at': max(f['filled_at'] for f in values)}
        pnl = children('realized_pnl', ticker, eid)
        known = pnl and all(p['amount'] is not None for p in pnl)
        realized = str(sum((Decimal(p['amount']) for p in pnl), Decimal(0))) if known else None
        lot = rt.execute('SELECT payload FROM te_lots WHERE id=?', (eid,)).fetchone()
        details.append({'execution': ex, 'entry': leg('ENTRY'), 'exit': leg('EXIT'),
                        'fills': fills, 'lot_remaining_quantity': json.loads(lot['payload']).get('remaining_qty') if lot else None,
                        'realized_pnl_usd': realized, 'realized_pnl_records': pnl,
                        'pnl_basis': 'realized net P&L from owned exit allocations; no mark-to-market estimate'})
    return {'intents': intentions, 'closed_candidates': candidates, 'executions': details}

print(json.dumps({'type': 'manifest', 'format': 'doxagent.w3-trade-intent-review.v1',
                  'as_of_utc': as_of.isoformat(), 'since_utc': since.isoformat(),
                  'window': '[since, as_of]', 'intent_count': len(selected_intents),
                  'adjacent_non_intents': adjacent_non_intents}, ensure_ascii=False), flush=True)
for intent_id, intent in sorted(selected_intents.items(), key=lambda x:(x[1]['ticker'],x[1]['released_at'],x[0])):
    ticker, case_id = intent['ticker'], intent['case_id']
    summary = current('case', ticker, case_id)
    native = current('native:runtime_v2_cases', ticker, case_id)
    if not summary or not native:
        raise ValueError('missing case evidence: ' + case_id)
    if not native.get('w3_result') or native['w3_result'] != intent['trade']['w3_result']:
        raise ValueError('W3 decision differs from Case evidence: ' + case_id)
    messages = children('message', ticker, case_id)
    if len(messages) != summary['member_count']:
        raise ValueError('case message count mismatch: ' + case_id)
    for message in messages:
        message['body_text'] = content_text(ticker, message['body'])
    case_trade = trade_info(ticker, case_id)
    if not any(i['intent_id'] == intent_id for i in case_trade['intents']):
        raise ValueError('selected intent absent from Case trade evidence: ' + intent_id)
    linked = [item for item in case_trade['executions']
              if item['execution']['intent_id'] == intent_id]
    if len(linked) > 1:
        raise ValueError('multiple execution summaries for one intent: ' + intent_id)
    execution = linked[0] if linked else None
    executor_row = rt.execute('SELECT 1 FROM te_executions WHERE id=?', (intent_id,)).fetchone()
    entry = execution['entry'] if execution else None
    exit_fill = execution['exit'] if execution else None
    pnl = execution['realized_pnl_usd'] if execution else None
    provisional = bool(execution and any(p['provisional'] for p in execution['realized_pnl_records']))
    trade_result = {
        'formal_intent_status': intent['status'],
        'case_read_trade_state': summary['trade']['state'],
        'case_read_intent_count': summary['trade']['intent_count'],
        'read_execution_state': execution['execution']['execution_state'] if execution else None,
        'read_execution_reason_codes': execution['execution']['execution_reason_codes'] if execution else [],
        'executor_execution_record_exists': bool(executor_row),
        'entry': entry,
        'exit': exit_fill,
        'realized_pnl_usd': pnl,
        'pnl_state': 'PROVISIONAL' if provisional else 'REALIZED' if pnl is not None else
                     'OPEN_OR_PARTIAL' if entry else 'NO_ENTRY_FILL',
        'read_projection_gap': summary['trade']['intent_count'] == 0 or execution is None,
    }
    record = {'intent_id': intent_id, 'ticker': ticker, 'case_id': case_id,
              'intent_released_at_utc': intent['released_at'],
              'intent_status': intent['status'],
              'decision_origin': intent['trade']['decision_origin'],
              'w3_case_id': intent['trade'].get('w3_case_id') or intent['trade']['w3_result']['w3_case_id'],
              'w3_decision': intent['trade']['w3_result'],
              'trade_direction': intent['trade']['decision'],
              'case_summary': summary,
              'case_process': {k: native.get(k) for k in ('created_at','completed_at','status','technical_status',
                                                          'runtime_mode','route','resolved_route','closed_cycle_id',
                                                          'sweep_id','trade_expired','w1_round1','w1_final',
                                                          'w2_round1','w2_final','w2_skipped','w3_result')},
              'source_messages': messages,
              'discovered_facts': children('candidate', ticker, case_id),
              'trade': case_trade,
              'trade_result': trade_result}
    print(json.dumps({'type': 'record', 'data': record}, ensure_ascii=False), flush=True)
rd.rollback(); rt.rollback()
