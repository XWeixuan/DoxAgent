"""Read-only export of frozen W1-R2 comparison context for the original sample."""
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
    source = (data.get('source') or {}).get('snapshot') or {}
    if (data.get('w1_final') or {}).get('result') == 'NEW' and len(source.get('body') or '') >= 300:
        by_ticker.setdefault(ticker, []).append((case_id, created_at))
results = []
for ticker in sorted(by_ticker):
    rows = by_ticker[ticker]
    for stratum in range(5):
        lo, hi = len(rows)*stratum//5, len(rows)*(stratum+1)//5
        case_id, created_at = rows[random.Random(f'20261010:{ticker}:{stratum}').randrange(lo, hi)]
        frozen_row = db.execute(
            'SELECT payload FROM runtime_values WHERE namespace=? AND key=?',
            ('round_inputs', case_id+':W1:R2')
        ).fetchone()
        frozen = json.loads(frozen_row[0]) if frozen_row else None
        results.append({'case_id':case_id,'ticker':ticker,'created_at':created_at,
                        'w1_r2':frozen})
print(json.dumps(results,ensure_ascii=False))
db.rollback()
