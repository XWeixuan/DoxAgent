"""Offline reconciliation from immutable ledger and IBKR tick snapshots."""
import csv, hashlib, json, statistics
from datetime import datetime
from decimal import Decimal
from pathlib import Path

root=Path(__file__).parent
plan=json.loads((root/'plan.json').read_text(encoding='utf8'))
market=[json.loads(l) for l in (root/'market.jsonl').read_text(encoding='utf8').splitlines()]
queries={r['target']['key']:r for r in market if r['kind']=='query'}
def dt(t):return datetime.fromisoformat(t.replace('Z','+00:00'))
def select(key,first_any=False):
    q=queries.get(key)
    if not q or 'error' in q:return None
    t=q['target'];at=dt(t['at']);gap=5 if t['kind']=='exit' else 60
    eligible=[r for r in q['response']['events'] if 0<=(dt(r['timestamp'])-at).total_seconds()<=gap and not r['past_limit'] and Decimal(str(r['price']))>0]
    normal=[r for r in eligible if not r['unreported']]
    pool=eligible if first_any else normal or eligible
    if not pool:return None
    r=min(pool,key=lambda r:dt(r['timestamp']))
    return dict(**r,offset_seconds=(dt(r['timestamp'])-at).total_seconds(),query_key=key)
def ret(a,b,direction):
    if not a or not b:return None
    a=Decimal(str(a['price']));b=Decimal(str(b['price']))
    return str((b-a)/a*100*(1 if direction=='LONG' else -1))
rows=[]
for r in plan['rows']:
    r=dict(r)
    for k in ['entry','exit','raw_entry','raw_exit']:r[k+'_tick']=select(r[k+'_key'])
    r['return_pct']=ret(r['entry_tick'],r['exit_tick'],r['direction'])
    r['raw_return_pct']=ret(r['raw_entry_tick'],r['raw_exit_tick'],r['direction'])
    r['first_any_return_pct']=ret(select(r['entry_key'],True),select(r['exit_key'],True),r['direction'])
    r['pricing_status']='PRICED' if r['return_pct'] is not None else 'NO_CONTEMPORANEOUS_ENTRY'
    rows.append(r)
def stats(subset,field='return_pct'):
    vals=[float(r[field]) for r in subset if r[field] is not None]
    return dict(count=len(subset),priced=len(vals),unpriced=len(subset)-len(vals),positive=sum(v>0 for v in vals),negative=sum(v<0 for v in vals),zero=sum(v==0 for v in vals),mean_pct=statistics.mean(vals) if vals else None,median_pct=statistics.median(vals) if vals else None,min_pct=min(vals) if vals else None,max_pct=max(vals) if vals else None)
summary=dict(window_end=plan['snapshot_at'],capped_count=sum(r['capped'] for r in rows),all=stats(rows),excluding_blocked_duplicate=stats([r for r in rows if r['status']!='DUPLICATE_POLICY']),original_release_time=stats(rows,'raw_return_pct'),first_any_tick_sensitivity=stats(rows,'first_any_return_pct'),by_ticker={t:stats([r for r in rows if r['ticker']==t]) for t in sorted(set(r['ticker'] for r in rows))})
assert len(rows)==3 and len(set(r['intent_id'] for r in rows))==3
assert len(queries)==len(plan['targets']) and all('error' not in q for q in queries.values())
cross=json.loads((root/'crosscheck.json').read_text(encoding='utf8'))
ids={r['intent_id'].removeprefix('trade:') for r in rows}
snapshot=json.loads((root/'snapshot.json').read_text(encoding='utf8'))
assert sum(bool(r['w3']) for r in snapshot['intents'])==2
assert all(r['intent']['trade'].get('w3_result',{}).get('expert_trade',{}).get('trade') for r in snapshot['intents'] if r['w3'])
assert sum(r['intent']['trade'].get('decision_origin')=='POLICY' for r in snapshot['intents'])==1
assert {r['row']['case_id'] for r in cross['trade_records']}=={r['intent_id'].removeprefix('trade:') for r in rows if r['status']!='DUPLICATE_POLICY'}
for r in rows:
    assert dt(r['case_created_at'])<=dt(r['entry_at'])<=dt(r['released_at'])<dt(r['exit_at'])
    assert (dt(r['entry_at'])-dt(r['case_created_at'])).total_seconds()<=300
    if r['entry_tick']:assert 0<=r['entry_tick']['offset_seconds']<=60
    if r['exit_tick']:assert 0<=r['exit_tick']['offset_seconds']<=5
(root/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
(root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
flat=[]
for n,r in enumerate(rows,1):
    flat.append(dict(number=n,**{k:r[k] for k in ['intent_id','ticker','direction','status','origin','case_created_at','released_at','latency_minutes','capped','entry_at','exit_at','return_pct','raw_return_pct','pricing_status','title']},entry_price=r['entry_tick']['price'] if r['entry_tick'] else None,exit_price=r['exit_tick']['price'] if r['exit_tick'] else None))
with (root/'intent_audit.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
manifest={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in ['snapshot.json','crosscheck.json','plan.json','market.jsonl','results.json','summary.json','intent_audit.csv']}
(root/'sha256.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
