"""Read-only, deterministic stratified sample from the production runtime DB."""
import json
import random
import sqlite3

db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
by_ticker = {}
for case_id, ticker, created_at, payload in db.execute(
    'SELECT case_id,ticker,created_at,payload_json FROM runtime_v2_cases ORDER BY created_at,case_id'
):
    data = json.loads(payload)
    if (data.get('w1_final') or {}).get('result') != 'NEW':
        continue
    source = (data.get('source') or {}).get('snapshot') or {}
    if len(source.get('body') or '') < 300:
        continue
    by_ticker.setdefault(ticker, []).append((case_id, created_at, data))

sample = []
for ticker in sorted(by_ticker):
    rows = by_ticker[ticker]
    for stratum in range(5):
        lo, hi = len(rows)*stratum//5, len(rows)*(stratum+1)//5
        rng = random.Random(f'20261010:{ticker}:{stratum}')
        case_id, created_at, data = rows[rng.randrange(lo, hi)]
        candidate_rows = db.execute(
            'SELECT candidate_identity,provisional_event_id,payload_json '
            'FROM runtime_v2_candidates WHERE source_message_id=? ORDER BY candidate_index',
            (data['source']['source_message_id'],)
        ).fetchall()
        candidates = []
        for identity, event_id, payload in candidate_rows:
            candidates.append({'identity': identity, 'event_id': event_id,
                               'payload': json.loads(payload)})
        sample.append({
            'case_id': case_id, 'ticker': ticker, 'created_at': created_at,
            'source_message_id': data['source']['source_message_id'],
            'source': data['source']['snapshot'],
            'w1_final': data.get('w1_final'),
            'w1_extraction': data.get('w1_extraction'),
            'w2_final': data.get('w2_final'),
            'w3_result': data.get('w3_result'),
            'route': data.get('route'),
            'candidates': candidates,
            'stratum': stratum+1,
        })
print(json.dumps({'method':'5 chronological equal-count strata per ticker, '
                         'one seeded-random NEW body >=300 case per stratum, '
                         'no provision count restriction',
                  'population':{k:len(v) for k,v in by_ticker.items()},
                  'sample':sample},ensure_ascii=False))
db.rollback()
