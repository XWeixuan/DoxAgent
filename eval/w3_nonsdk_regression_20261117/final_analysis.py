import json,gzip,math,statistics
from pathlib import Path
p=Path(__file__).parent;a=json.loads((p/'analysis.json').read_text());raw=json.load(gzip.open(p/'cohort.json.gz'));byid={x['case_id']:x for x in raw['cases']}
def stat(xs):
 xs=sorted(v for v in xs if v is not None)
 return dict(n=len(xs),mean=statistics.mean(xs),p50=statistics.median(xs),p95=xs[math.ceil(len(xs)*.95)-1],max=xs[-1]) if xs else dict(n=0)
rows=a['rows'];valid=[r for r in rows if r['hot_missing']==0 and r['sdk']==r['trade_jobs']]
out=dict(cohort_at=raw['at'],candidate_count=raw['candidate_count'],oldest_candidate=raw['oldest_candidate'],routed_cases=len(raw['cases']),trade_completed=len(rows),incomplete_model_intervals=[r['case_id'] for r in rows if r not in valid],groups={},tickers={},rows=valid)
for name,pred in {'yesterday':lambda x:x['day']=='yesterday','today':lambda x:x['day']=='today','today_early':lambda x:'2026-11-17T07:00'<=x['created']<'2026-11-17T07:25','today_later':lambda x:x['created']>='2026-11-17T07:30'}.items():
 xs=[r for r in valid if pred(r)];out['groups'][name]={k:stat([r.get(k) for r in xs]) for k in ('ui_non_model','pre_w3','w3_internal','ops_internal','whole_w3','ready_to_first_job','dispatch_to_sdk','tail')}
for ticker in sorted({r['ticker'] for r in valid}):
 xs=[r for r in valid if r['day']=='today' and r['ticker']==ticker];out['tickers'][ticker]={k:stat([r.get(k) for r in xs]) for k in ('ui_non_model','pre_w3','w3_internal')}
out['interval_bias_max']=max(abs(r['interval_bias']) for r in valid)
out['ui_record_delta_max']=max(abs(r['ui_non_model']-float(byid[r['case_id']]['ui_case']['non_model_seconds'])) for r in valid if byid[r['case_id']]['ui_case'].get('non_model_seconds') is not None)
(p/'findings.json').write_text(json.dumps(out,indent=2));print(json.dumps({k:v for k,v in out.items() if k not in ('rows','tickers')},indent=2));print('TICKERS',[(t,v['ui_non_model'].get('n'),v['ui_non_model'].get('mean')) for t,v in out['tickers'].items()])
