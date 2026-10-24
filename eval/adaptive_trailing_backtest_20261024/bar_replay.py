"""Indicative 5s BBO-close replay, NOT a certified fresh-size quote replay.

Each bar is revealed at its END. No intrabar extrema update stops. Exact
timestamp/size freshness is unavailable in BID/ASK bars and remains a limitation.
"""
import csv,json,math
from pathlib import Path
import replay as r
ROOT=Path(__file__).parent

def rows(name):
    p=ROOT/name
    if p.exists():
        for line in p.read_text(encoding='utf8').splitlines():
            if line.strip():yield json.loads(line)

class BarQuotes:
    def __init__(self):
        self.series={}
        for name in ['five_second_quotes.jsonl','five_second_retry.jsonl','overnight_quotes.jsonl']:
            for row in rows(name):
                if 'response' not in row:continue
                key=(row['symbol'],row['show'])
                self.series.setdefault(key,{}).update({int(b['date'])+5:b for b in row['response']['bars']})
        self.available={s:set(self.series.get((s,'BID'),{}))&set(self.series.get((s,'ASK'),{})) for s in ['RKLB','QQQ']}
    def get(self,s,t):
        if t not in self.available[s]:return None
        b=self.series[s,'BID'][t];a=self.series[s,'ASK'][t]
        return dict(timestamp=r.iso(t-1),bid_price=b['close'],ask_price=a['close'],bid_size=1,ask_size=1,
                    size_is_placeholder=True,freshness_unverified=True,source='completed_5s_bid_ask_bars')

def entry_quotes():
    states={}
    for row in rows('probe.jsonl'):
        if row['kind']=='bid_ask_probe':states[row['symbol']]={r.ts(e['timestamp']):e for e in row['response']['events']}
    return r.Quotes(states)

def fixed_quotes():
    states={}
    for row in rows('warmup_and_exit.jsonl'):
        if row['kind']=='fixed_exit_quotes':states[row['symbol']]={r.ts(e['timestamp']):e for e in row['response']['events']}
    return r.Quotes(states)

def case1():
    q=entry_quotes();eq=fixed_quotes();start=r.ts('2026-10-14T11:30:00+00:00');end=r.ts('2026-10-14T19:30:00+00:00')
    for t in range(start,start+61,5):
        st=q.get('INTC',t);et=q.get('SOXX',t)
        if r.valid(st,t)[0] and r.valid(et,t)[0]:break
    else:raise RuntimeError('no case1 synchronized entry')
    sb=float(st['bid_price']);ea=float(et['ask_price']);out={}
    extrema={}
    for row in rows('quote_bounds.jsonl'):
        if row['kind']=='quote_bounds':
            bars=[b for b in row['response']['bars'] if (t//60)*60<=int(b['date'])<end]
            extrema[row['symbol']]=min(b['low'] for b in bars) if row['symbol']=='INTC' else max(b['high'] for b in bars)
    for hedge in [False,True]:
        qe=r.N*.66*1.1983939685328249/ea if hedge else 0
        pos=dict(start=t,stock_entry=sb,etf_entry=ea,qs=-r.N/sb,qe=qe,hedged=hedge,entry_commission=1+bool(hedge))
        sx=eq.get('INTC',end);ex=eq.get('SOXX',end)
        assert r.valid(sx,end)[0] and r.valid(ex,end)[0]
        net=r.valuation(pos,sx,ex,end)
        upper=pos['qs']*(extrema['INTC']-sb)+qe*(extrema['SOXX']-ea)
        out['beta66' if hedge else 'unhedged']=dict(position=pos,entry_utc=r.iso(t),fixed_exit_utc=r.iso(end),fixed_net=net,
             fixed_stock_ask=sx['ask_price'],fixed_etf_bid=ex['bid_price'],gross_mfe_upper_bound=upper,
             activation_impossible=upper<.05*r.N,extrema=extrema)
    return out

def crossvalidate(bq):
    states,coverage,_,_=r.load();tq=r.Quotes(states);out={}
    for s in ['RKLB','QQQ']:
        n=0;exact=0;maxdiff=0
        for t in sorted(bq.available[s]):
            if not r.START<=t<coverage[s]:continue
            tick=tq.get(s,t);bar=bq.get(s,t)
            if not tick:continue
            # Tick events are integer-second stamped: exclude boundary-second
            # events from both comparisons to avoid making up subsecond order.
            if r.ts(tick['timestamp'])==t-1:continue
            diff=max(abs(tick['bid_price']-bar['bid_price']),abs(tick['ask_price']-bar['ask_price']))
            n+=1;exact+=diff<1e-7;maxdiff=max(maxdiff,diff)
        out[s]=dict(comparable_snapshots=n,exact_matches=exact,max_abs_price_difference=maxdiff)
    return out

def main():
    q=BarQuotes();entry=entry_quotes();fixed=fixed_quotes()
    result=dict(label='CONDITIONAL_5S_PRICE_REPLAY_FRESHNESS_AND_SIZE_UNVERIFIED',case1=case1(),case2={},crossvalidation=crossvalidate(q))
    # Never forward-fill missing sessions. A missing overnight interval means
    # an unresolved earlier trigger could exist; mark it explicitly below.
    for hedge in [False,True]:
        key='beta66' if hedge else 'unhedged';pos=r.make_position(entry,hedge)
        result['case2'][key]={}
        reference=r.replay_position(q,pos,r.FIXED_END,1.,2.5)
        full_mfe=reference['mfe_at_exit_or_coverage']
        for name,(coef,mult) in r.PARAMS.items():
            model=r.replay_position(q,pos,r.FIXED_END,coef,mult);trace=model.pop('trace')
            stop=model['trigger']['time'] if model['trigger'] else r.FIXED_END
            gaps=[(a,b) for a,b in zip(sorted(q.available['RKLB']&q.available['QQQ']),sorted(q.available['RKLB']&q.available['QQQ'])[1:]) if a>=r.START and b<=stop and b-a>5]
            model['missing_joint_intervals_before_exit']=[dict(after=r.iso(a),next=r.iso(b),missing_seconds=b-a-5) for a,b in gaps]
            model['strict_quote_validation']='UNAVAILABLE_SIZE_AND_FRESHNESS'
            model['net_exit']=model['trigger']['net_pnl'] if model['trigger'] else r.valuation(pos,fixed.get('RKLB',r.FIXED_END),fixed.get('QQQ',r.FIXED_END),r.FIXED_END)
            model['fixed_exit_net']=r.valuation(pos,fixed.get('RKLB',r.FIXED_END),fixed.get('QQQ',r.FIXED_END),r.FIXED_END)
            model['delta_vs_fixed']=model['net_exit']-model['fixed_exit_net']
            model['retention_of_pre_exit_filtered_mfe']=model['net_exit']/model['mfe_at_exit_or_coverage'] if model['mfe_at_exit_or_coverage'] else None
            model['full_fixed_horizon_filtered_mfe']=full_mfe
            model['retention_of_full_horizon_filtered_mfe']=model['net_exit']/full_mfe if full_mfe else None
            model['fixed_retention_of_full_horizon_mfe']=model['fixed_exit_net']/full_mfe if full_mfe else None
            with (ROOT/f'bar_trace_{key}_{name}.csv').open('w',encoding='utf8',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(trace[0]));w.writeheader();w.writerows(trace)
            result['case2'][key][name]=model
            print(key,name,'activation',model['activation_utc'],'trigger',model['trigger'] and model['trigger']['time_utc'],'net',round(model['net_exit'],2),'delta',round(model['delta_vs_fixed'],2),'gaps',len(gaps))
    (ROOT/'bar_results.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print('case1',json.dumps(result['case1']));print('crossvalidate',result['crossvalidation'])
if __name__=='__main__':main()
