"""Read-only production export. Pipe stdout to split_local.py; never writes remotely."""
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

# Include true observations from historical revisions as well as current state.
observations = {}
for table in ('object_current', 'objects'):
    for row in rd.execute("SELECT ticker,id,parent,payload FROM " + table +
                          " WHERE kind='policy_hit_observation' AND json_extract(payload,'$.hit')=1"):
        value = json.loads(row['payload'])
        at = value.get('occurred_at')
        if at and since <= iso(at) <= as_of:
            key = (row['ticker'], row['id'])
            observations.setdefault(key, {'ticker': row['ticker'], 'id': row['id'],
                                          'case_id': row['parent'], 'hit': value})

# Runtime records are few; preload the case links without copying frozen source bodies into output.
runtime_values = {}
for row in rt.execute("SELECT namespace,payload FROM runtime_values WHERE namespace IN ('trade_intents','candidates')"):
    value = json.loads(row['payload'])
    case_id = value.get('case_id') or (value.get('trade') or {}).get('case_id')
    if case_id:
        runtime_values.setdefault(case_id, {}).setdefault(row['namespace'], []).append(value)

def pinned_policy(ticker, native_case, policy_id):
    pin = native_case.get('version_pin') or {}
    activation = current('activation_revision', ticker, pin.get('activation_revision_id', ''))
    artifact = ((activation or {}).get('policy_set') or {}).get('artifact_id')
    detail = None
    if artifact:
        for table in ('object_current', 'objects'):
            row = rd.execute("SELECT payload FROM " + table +
                             " WHERE kind='policy_detail' AND ticker=? AND parent=? "
                             "AND json_extract(payload,'$.summary.policy_id')=? ORDER BY valid_from DESC LIMIT 1",
                             (ticker, artifact, policy_id)).fetchone()
            if row:
                detail = decode(json.loads(row['payload']))
                break
    frozen = next((p for p in native_case['frozen_inputs']['projection']['policies']
                   if p['policy_id'] == policy_id), None)
    if frozen is None:
        raise ValueError('hit policy missing in frozen inputs: ' + policy_id)
    return {'frozen_match': frozen, 'pinned_detail': detail,
            'policy_set_version': pin.get('policy_set_version'),
            'activation_revision_id': pin.get('activation_revision_id')}

def trade_info(ticker, case_id):
    executions = children('execution', ticker, case_id)
    intentions = []
    for item in runtime_values.get(case_id, {}).get('trade_intents', []):
        trade = item.get('trade') or {}
        intentions.append({k: item.get(k) for k in ('intent_id','status','released_at','selection_id','submitted')})
        intentions[-1].update({'direction': trade.get('decision'),
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

print(json.dumps({'type': 'manifest', 'format': 'doxagent.policy-hit-review.v1',
                  'as_of_utc': as_of.isoformat(), 'since_utc': since.isoformat(),
                  'window': '[since, as_of]', 'hit_count': len(observations)}, ensure_ascii=False), flush=True)
for (ticker, hit_id), observation in sorted(observations.items(), key=lambda x:(x[0][0],x[1]['hit']['occurred_at'],x[0][1])):
    case_id = observation['case_id']
    summary = current('case', ticker, case_id)
    native = current('native:runtime_v2_cases', ticker, case_id)
    if not summary or not native:
        raise ValueError('missing case evidence: ' + case_id)
    messages = children('message', ticker, case_id)
    if len(messages) != summary['member_count']:
        raise ValueError('case message count mismatch: ' + case_id)
    for message in messages:
        message['body_text'] = content_text(ticker, message['body'])
    current_hit = current('policy_hit_observation', ticker, hit_id)
    policy_id = observation['hit']['policy_id']
    case_trade = trade_info(ticker, case_id)
    executed_ids = {i['executed_policy_id'] for i in case_trade['intents'] if i.get('executed_policy_id')}
    record = {'policy_hit_id': hit_id, 'ticker': ticker, 'case_id': case_id,
              'hit_observed_at_utc': observation['hit']['occurred_at'],
              'hit_current': current_hit['hit'] if current_hit else None,
              'policy_id': policy_id, 'activation_revision': observation['hit']['ar'],
              'policy': pinned_policy(ticker, native, policy_id),
              'hit_policy_released_for_trade': policy_id in executed_ids,
              'case_summary': summary,
              'case_process': {k: native.get(k) for k in ('created_at','completed_at','status','technical_status',
                                                          'runtime_mode','route','resolved_route','closed_cycle_id',
                                                          'sweep_id','trade_expired','w1_round1','w1_final',
                                                          'w2_round1','w2_final','w2_skipped','w3_result')},
              'source_messages': messages,
              'discovered_facts': children('candidate', ticker, case_id),
              'trade': case_trade}
    print(json.dumps({'type': 'record', 'data': record}, ensure_ascii=False), flush=True)
rd.rollback(); rt.rollback()
