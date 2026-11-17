import gzip,json,math,statistics
from datetime import datetime
from pathlib import Path
p=Path(__file__).parent;data=json.load(gzip.open(p/'cohort.json.gz'))
def ts(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp() if s else None
def union(xs,a,b):
 out=[]
 for x,y in sorted((max(x,a),min(y,b)) for x,y in xs if y>a and x<b):
  if out and x<=out[-1][1]:out[-1][1]=max(out[-1][1],y)
  else:out.append([x,y])
 return sum(y-x for x,y in out)
def stats(xs):
 xs=sorted(x for x in xs if x is not None)
 return dict(n=len(xs),mean=statistics.mean(xs),p50=statistics.median(xs),p95=xs[math.ceil(len(xs)*.95)-1],max=xs[-1]) if xs else dict(n=0)
rows=[]
for c in data['cases']:
 stages={s['phase']:s for s in c['stages']};trade=stages.get('trade_decision');ops=stages.get('post_decision_operations');effects={s['effect_type']:s for s in c['effects']};ef=effects.get('route_to_w3');of=effects.get('w3_post_decision')
 if not trade or trade['status']!='COMMITTED' or not ef or not ef['effective_at']:continue
 phases={a['attempt_id']:a.get('phase') for a in c['attempts'] if a.get('node_id')=='W3'}
 intervals=[(ts(a['model_interval']['start']),ts(a['model_interval']['end'])) for a in c['attempts'] if a.get('phase')!='post_decision_operations' and (a.get('model_interval') or {}).get('start') and (a.get('model_interval') or {}).get('end')]
 hot=[(ts(a['model_interval']['start']),ts(a['model_interval']['end'])) for a in c['attempts'] if a.get('node_id')!='W3' and (a.get('model_interval') or {}).get('start') and (a.get('model_interval') or {}).get('end')]
 jobs=c['jobs'];sdk=[];osdk=[];tj=[];oj=[]
 for j in jobs:
  t=j['timing'];phase=phases.get(j['id']) or j['phase']
  if phase=='trade_decision':tj.append(j)
  elif phase=='post_decision_operations':oj.append(j)
  if t.get('sdk_dispatched_at') and t.get('sdk_completed_at'):
   z=(ts(t['sdk_dispatched_at']),ts(t['sdk_completed_at']))
   if phase=='trade_decision':sdk.append(z)
   elif phase=='post_decision_operations':osdk.append(z)
 a,b,start=ts(c['created_at']),ts(ef['effective_at']),ts(ef['created_at'])
 covered=union(intervals,a,b);truth=union(hot+sdk,a,b);w3internal=b-start-union(sdk,start,b)
 pre=start-a-union(hot,a,start);modelids={a['attempt_id'] for a in c['attempts'] if a.get('phase')=='trade_decision'}
 row=dict(case_id=c['case_id'],ticker=c['ticker'],created=c['created_at'],day='today' if c['created_at']>='2026-11-17T07:00' else 'yesterday',wall=b-a,ui_non_model=b-a-covered,raw_non_model=b-a-truth,interval_bias=truth-covered,pre_w3=pre,w3_internal=w3internal,sdk=len(sdk),trade_attempts=len(modelids),trade_jobs=len(tj),hot_missing=sum(not ((a.get('model_interval') or {}).get('start') and (a.get('model_interval') or {}).get('end')) for a in c['attempts'] if a.get('phase')!='post_decision_operations'),ready_to_first_job=min([ts(j['created']) for j in tj],default=start)-start,dispatch_to_sdk=min([ts(j['timing'].get('sdk_dispatched_at')) for j in tj if j['timing'].get('sdk_dispatched_at')],default=start)-min([ts(j['created']) for j in tj],default=start),tail=b-max([ts(j['timing'].get('sdk_completed_at')) for j in tj if j['timing'].get('sdk_completed_at')],default=b))
 if ops and ops['status']=='COMMITTED' and of and osdk:
  oa=max(ts(of['created_at']),ts(trade['committed_at']));ob=max(ts(ops['committed_at']),ts(of['updated_at']));row['ops_internal']=ob-oa-union(osdk,oa,ob);row['whole_w3']=ob-start-union(sdk+osdk,start,ob)
 rows.append(row)
summary={}
for day in ('today','yesterday'):
 xs=[r for r in rows if r['day']==day];summary[day]={k:stats([r.get(k) for r in xs]) for k in ('ui_non_model','raw_non_model','interval_bias','pre_w3','w3_internal','ops_internal','whole_w3','ready_to_first_job','dispatch_to_sdk','tail')};summary[day]['tickers']={t:{k:stats([r.get(k) for r in xs if r['ticker']==t]) for k in ('ui_non_model','raw_non_model','pre_w3','w3_internal','ops_internal')} for t in sorted({r['ticker'] for r in xs})}
(p/'analysis.json').write_text(json.dumps(dict(summary=summary,rows=rows),indent=2))
print(json.dumps({k:{a:b for a,b in v.items() if a!='tickers'} for k,v in summary.items()},indent=2));print('TODAY_TICKERS',json.dumps(summary['today']['tickers']));print('SLOW_TODAY',json.dumps(sorted([r for r in rows if r['day']=='today'],key=lambda r:r['raw_non_model'],reverse=True)[:8]));print('BIASES',sorted([r['interval_bias'] for r in rows])[-5:])
