"""Freeze ETF selection before the sample; roll relative intraday Beta pre-entry."""
import json,math
from datetime import datetime,UTC
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
root=Path(__file__).parent;source=root.parent/'trade_intent_return_audit_20261022';et=ZoneInfo('America/New_York')
rows=json.loads((root/'audit_combined.json').read_text(encoding='utf8'))['intents']
raw=[json.loads(l) for l in (root/'history.jsonl').read_text(encoding='utf8').splitlines()]
hist={r['symbol']:r for r in raw if r['kind']=='history'}
assert len(hist)==11 and all('response' in h for h in hist.values()),[(k,v.get('error')) for k,v in hist.items()]
returns={}
for symbol,h in hist.items():
    bars={int(b['date']):b for b in h['response']['bars'] if b['volume']>0 and b['close']>0}
    rs={}
    for t,b in bars.items():
        previous=bars.get(t-1800)
        if previous and datetime.fromtimestamp(t,UTC).astimezone(et).date()==datetime.fromtimestamp(t-1800,UTC).astimezone(et).date():
            rs[t]=(float(b['close'])/float(previous['close'])-1,datetime.fromtimestamp(t,UTC).astimezone(et).date().isoformat())
    returns[symbol]=rs
def estimate(stock,etf,before,days=30,bootstrap=False):
    pairs=[(t,returns[etf][t][0],v[0],v[1]) for t,v in returns[stock].items() if t in returns[etf] and v[1]<before]
    dates=sorted({p[3] for p in pairs})[-days:];pairs=sorted(p for p in pairs if p[3] in dates)
    assert len(dates)==days and len(pairs)>=days*8,(stock,etf,before,len(dates),len(pairs))
    x=np.array([p[1] for p in pairs]);y=np.array([p[2] for p in pairs])
    beta=float(np.cov(x,y,ddof=1)[0,1]/np.var(x,ddof=1));intercept=float(y.mean()-beta*x.mean())
    assert abs(float(np.linalg.lstsq(np.c_[np.ones(len(x)),x],y,rcond=None)[0][1])-beta)<1e-10
    result=dict(stock=stock,etf=etf,before_day=before,sessions=days,observations=len(pairs),first_day=dates[0],last_day=dates[-1],max_bar_end_utc=datetime.fromtimestamp(max(p[0] for p in pairs)+1800,UTC).isoformat(),beta=beta,correlation=float(np.corrcoef(x,y)[0,1]),r_squared=float(np.corrcoef(x,y)[0,1]**2),intercept_per_bar=intercept)
    if bootstrap:
        rng=np.random.default_rng(231023);groups={d:[i for i,p in enumerate(pairs) if p[3]==d] for d in dates};bs=[]
        for _ in range(1000):
            idx=np.concatenate([groups[dates[i]] for i in rng.integers(0,days,size=days)])
            xx=x[idx];yy=y[idx];bs.append(float(np.cov(xx,yy,ddof=1)[0,1]/np.var(xx,ddof=1)))
        result['beta_ci95_day_bootstrap']=[float(v) for v in np.quantile(bs,[.025,.975])]
    return result
freeze=min(datetime.fromisoformat(r['entry_at']).astimezone(et).date().isoformat() for r in rows)
candidate_sets={'COHR':['SOXX','XLK','QQQ'],'NBIS':['SKYY','IGV','QQQ']}
selection={};mapping={'MU':'SOXX','INTC':'SOXX','BE':'SOXX','RKLB':'QQQ'}
for stock,candidates in candidate_sets.items():
    fits=[estimate(stock,e,freeze) for e in candidates]
    best=max(fits,key=lambda v:v['r_squared']);mapping[stock]=best['etf'];selection[stock]=dict(chosen=best['etf'],frozen_before=freeze,candidates=fits)
fits=[]
for r in rows:
    day=datetime.fromisoformat(r['entry_at']).astimezone(et).date().isoformat();stock=r['ticker'];hedge=mapping[stock]
    primary=estimate(stock,hedge,day,30,True);alternates={str(d):estimate(stock,hedge,day,d) for d in [20,60]}
    assert datetime.fromisoformat(primary['max_bar_end_utc'])<datetime.fromisoformat(r['entry_at'])
    assert primary['beta']>0
    fits.append(dict(intent_id=r['intent_id'],report_number=r['report_number'],primary=primary,alternates=alternates))
out=dict(estimator='OLS with intercept on paired within-session 30-minute simple returns; prior completed RTH sessions only',mapping=mapping,selection=selection,fits=fits)
(root/'beta.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print('mapping',mapping)
for stock,v in selection.items():print('ETF selection',stock,[(f['etf'],round(f['r_squared'],3),round(f['beta'],3)) for f in v['candidates']])
for f in fits:print(f['report_number'],f['primary']['stock'],f['primary']['etf'],'beta',round(f['primary']['beta'],3),'R2',round(f['primary']['r_squared'],3),'N',f['primary']['observations'],'20/60',[round(f['alternates'][d]['beta'],3) for d in ['20','60']])
