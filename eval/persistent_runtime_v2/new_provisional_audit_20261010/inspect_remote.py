import sqlite3

db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
for name in ('runtime_v2_cases', 'runtime_v2_candidates', 'runtime_v2_provisional_facts', 'runtime_v2_w3_cases', 'runtime_values'):
    row = db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('table', name)).fetchone()
    print(name, row[0] if row else 'MISSING')
print('case_count', db.execute('SELECT COUNT(*) FROM runtime_v2_cases').fetchone()[0])
print('ticker_counts', db.execute('SELECT ticker,COUNT(*) FROM runtime_v2_cases GROUP BY ticker').fetchall())
import json
from collections import Counter
stats=Counter(); samples=[]
for case_id,ticker,created,payload in db.execute('SELECT case_id,ticker,created_at,payload_json FROM runtime_v2_cases ORDER BY created_at'):
    d=json.loads(payload)
    w1=d.get('w1_final') or {}
    if w1.get('result')!='NEW': continue
    src=(d.get('source') or {}).get('snapshot') or {}
    body=src.get('body') or ''
    stats[(ticker,created[:10],'body' if len(body)>=300 else 'short')]+=1
    if len(samples)<3: samples.append({'case_id':case_id,'keys':list(d),'source':src,'w1':w1,'extraction':d.get('w1_extraction'),'w3':d.get('w3_result')})
print('new_stats',sorted((list(k),v) for k,v in stats.items()))
print('examples',json.dumps(samples,ensure_ascii=False)[:7000])
