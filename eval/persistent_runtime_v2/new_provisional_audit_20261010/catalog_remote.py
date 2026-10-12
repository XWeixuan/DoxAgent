import json
import sqlite3
from collections import Counter

db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
stats = Counter()
per_ticker = {}
for case_id, ticker, created, payload in db.execute(
    'SELECT case_id,ticker,created_at,payload_json FROM runtime_v2_cases ORDER BY created_at'
):
    data = json.loads(payload)
    w1 = data.get('w1_final') or {}
    if w1.get('result') != 'NEW':
        continue
    source = (data.get('source') or {}).get('snapshot') or {}
    body = source.get('body') or ''
    if len(body) < 300:
        continue
    extraction = (data.get('w1_extraction') or {}).get('candidates') or []
    w3 = ((data.get('w3_result') or {}).get('delta_candidates') or [])
    count = db.execute('SELECT COUNT(*) FROM runtime_v2_candidates WHERE source_message_id=?',
                       ((data.get('source') or {}).get('source_message_id'),)).fetchone()[0]
    route = (data.get('route') or {}).get('primary_route')
    key = (ticker, route, len(extraction), len(w3), count)
    stats[key] += 1
    per_ticker.setdefault(ticker, []).append((created, case_id, source.get('title'),
                                                len(body), len(extraction), len(w3), count,
                                                route))
print('stats', sorted(((list(key), value) for key, value in stats.items()), key=lambda x: str(x[0])))
for ticker, rows in per_ticker.items():
    print('ticker', ticker, 'count', len(rows))
    for i in [0, len(rows)//4, len(rows)//2, 3*len(rows)//4, len(rows)-1]:
        print('point', rows[i])
