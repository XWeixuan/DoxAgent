"""Reprice the frozen decisions at supplied disclosure clocks; do not re-decide W3."""
import csv,json,statistics
from datetime import datetime,UTC,timedelta,time
from pathlib import Path
import numpy as np
import engine as E
import beta_model as BM

ROOT=Path(__file__).parent;CLOCK={r['report_number']:r for r in json.loads((ROOT/'disclosure_clock.json').read_text(encoding='utf8'))}
NEW_BETA={r['report_number']:r['primary'] for r in json.loads((ROOT/'disclosure_beta.json').read_text(encoding='utf8'))['fits']}
OLD_FITS={r['report_number']:r['primary'] for r in E.BETA['fits']}
def next1530(t):
    local=datetime.fromtimestamp(t,UTC).astimezone(E.ET);day=local.date()
    if local.time()>=time(15,30):day+=timedelta(days=1)
    while day.weekday()>4:day+=timedelta(days=1)
    return int(datetime.combine(day,time(15,30),E.ET).timestamp())
FIT_CACHE={}
def build(clock='disclosure',exit_mode='fixed',beta_mode='refit',include_duplicate=False,omit=(),minute_offset=0,nf3_confirmation=False,smart_only=False,fresh_only=False):
    rows=[]
    for a in E.AUDIT:
        n=a['report_number']
        if n in omit or (a['status']=='DUPLICATE_POLICY' and not include_duplicate):continue
        c=CLOCK[n]
        if clock=='original':start=E.ceilminute(E.dt(a['entry_at']));fit=OLD_FITS[n]
        else:
            stamp=E.dt(c['disclosure_utc'])
            if nf3_confirmation and n==5:stamp+=timedelta(minutes=18)
            start=E.ceilminute(stamp+timedelta(minutes=minute_offset))
            if beta_mode=='frozen':fit=OLD_FITS[n]
            else:
                day=datetime.fromtimestamp(start,UTC).astimezone(E.ET).date().isoformat();key=(a['ticker'],day)
                if key not in FIT_CACHE:FIT_CACHE[key]=BM.estimate(a['ticker'],E.BETA['mapping'][a['ticker']],day,30)
                fit=FIT_CACHE[key]
        end=int(E.dt(a['exit_at']).timestamp()) if exit_mode=='fixed' else next1530(start)
        stock=a['ticker'];hedge=fit['etf']
        if smart_only or fresh_only:
            common=sorted(t for t in set(E.DATA[stock])&set(E.DATA[hedge]) if start<=t<end and (not smart_only or E.VENUES[(stock,t)]=='SMART' and E.VENUES[(hedge,t)]=='SMART') and E.DATA[stock][t]['volume']>0 and E.DATA[hedge][t]['volume']>0)
            assert common;start=common[0]
            day=datetime.fromtimestamp(start,UTC).astimezone(E.ET).date().isoformat();fit=BM.estimate(stock,hedge,day,30)
        assert start<end and E.bar(stock,start) and E.bar(hedge,start),(n,start,end)
        assert E.bar(stock,end) and E.bar(hedge,end)
        assert E.dt(fit['max_bar_end_utc']).timestamp()<start
        p0=E.bar(stock,start)['open'];p1=E.bar(stock,end)['open'];e0=E.bar(hedge,start)['open'];e1=E.bar(hedge,end)['open'];sign=1 if a['direction']=='LONG' else -1
        rows.append(dict(report_number=n,intent_id=a['intent_id'],ticker=stock,etf=hedge,direction=a['direction'],event_type=E.EVENTS[n],cluster='MODAL' if n in [6,7] else 'NEUTRON' if n in [2,3] else str(n),disclosure_utc=c['disclosure_utc'],disclosure_note=c['note'],start=start,end=end,start_utc=datetime.fromtimestamp(start,UTC).isoformat(),end_utc=datetime.fromtimestamp(end,UTC).isoformat(),original_entry_utc=a['entry_at'],released_at=a['released_at'],beta=fit['beta'],beta_r2=fit['r_squared'],beta_last_day=fit['last_day'],beta_max_bar_end_utc=fit['max_bar_end_utc'],stock_entry=p0,stock_exit=p1,etf_entry=e0,etf_exit=e1,stock_return_pct=sign*(p1/p0-1)*100,etf_raw_return_pct=(e1/e0-1)*100,overnights=(datetime.fromtimestamp(end,UTC).astimezone(E.ET).date()-datetime.fromtimestamp(start,UTC).astimezone(E.ET).date()).days,entry_stock_volume=E.bar(stock,start)['volume'],entry_etf_volume=E.bar(hedge,start)['volume'],stock_entry_venue=E.VENUES[(stock,start)],etf_entry_venue=E.VENUES[(hedge,start)],minutes_from_disclosure=(start-E.dt(c['disclosure_utc']).timestamp())/60))
    return rows
def run(save_prefix=None,equal_gross=False,**kwargs):
    rows=build(**kwargs);out=dict(trades={},summaries={})
    for k in E.LEVELS:
        key=str(k);trades=[E.trade(r,k,equal_gross=equal_gross) for r in rows];out['trades'][key]=trades;out['summaries'][key]=E.portfolio(trades,ROOT/f'equity_{save_prefix}_{key}.csv' if save_prefix else None)
    return out
def main():
    primary=run(save_prefix='disclosure');original=run(clock='original',save_prefix='original');natural=run(exit_mode='natural',save_prefix='natural')
    assert len(primary['trades']['0'])==15 and len(original['trades']['0'])==15
    assert {r['report_number'] for r in primary['trades']['0']}=={r['report_number'] for r in original['trades']['0']}
    for k in ['0','0.66','1.0']:
        for a,b in zip(primary['trades'][k],original['trades'][k]):assert a['end']==b['end']
    sensitivities={
        'freeze_original_beta':run(beta_mode='frozen'),
        'one_minute_after_disclosure':run(minute_offset=1),
        'five_minutes_after_disclosure':run(minute_offset=5),
        'nf3_explicit_1733':run(nf3_confirmation=True),
        'remove_second_modal':run(omit=(7,)),
        'remove_neutron_case2':run(omit=(2,)),
        'include_blocked_duplicate':run(include_duplicate=True),
        'same_gross_10000':run(equal_gross=True),
        'SMART_only_delay':run(smart_only=True),
        'first_common_traded_minute':run(fresh_only=True),
        'original_14_matched':run(clock='original',omit=(9,)),
        'disclosure_14_matched':run(omit=(9,)),
    }
    attribution={}
    for dim in ['ticker','direction','event_type']:
        attribution[dim]={}
        for value in sorted({r[dim] for r in primary['trades']['0']}):
            attribution[dim][value]={k:dict(count=sum(t[dim]==value for t in ts),net_pnl=sum(t['net_pnl'] for t in ts if t[dim]==value),wins=sum(t['net_pnl']>0 for t in ts if t[dim]==value)) for k,ts in primary['trades'].items()}
    # Keep messages sharing an ET disclosure date in the same resampled block.
    dates=sorted({E.dt(t['disclosure_utc']).astimezone(E.ET).date().isoformat() for t in primary['trades']['0']});rng=np.random.default_rng(241024);bootstrap={}
    for key in ['0','0.66','1.0']:
        base=original['trades'][key];new=primary['trades'][key];diffs={d:0. for d in dates};levels={d:0. for d in dates}
        for a,b in zip(base,new):
            d=E.dt(b['disclosure_utc']).astimezone(E.ET).date().isoformat();diffs[d]+=b['net_pnl']-a['net_pnl'];levels[d]+=b['net_pnl']
        ix=rng.integers(0,len(dates),size=(10000,len(dates)));v=np.array([diffs[d] for d in dates]);w=np.array([levels[d] for d in dates])
        bootstrap[key]=dict(disclosure_day_clusters=len(dates),delta_net_pnl_ci95=[float(z) for z in np.quantile(v[ix].sum(axis=1),[.025,.975])],net_pnl_ci95=[float(z) for z in np.quantile(w[ix].sum(axis=1),[.025,.975])])
    out=dict(clock_basis='supplied screenshot; not independently re-verified here',minute_convention='supplied disclosure-minute open; exact-second #9 rounded forward to a full minute; separate +1/+5 minute sensitivities',primary_exit='original scheduled exit unchanged',primary=primary,matched_original=original,natural_exit=natural,sensitivities=sensitivities,attribution=attribution,bootstrap=bootstrap)
    (ROOT/'results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
    with (ROOT/'trades.csv').open('w',encoding='utf-8-sig',newline='') as f:
        rows=[dict(scenario=name,**t) for name,sc in [('disclosure',primary),('original',original),('natural_exit',natural)] for ts in sc['trades'].values() for t in ts];w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    for name,scenario in [('original15',original),('disclosure15',primary),('natural',natural)]:
        print(name,{k:{m:round(v[m],4) for m in ['gross_pnl','net_pnl','win_rate_pct','maximum_drawdown_pct','trade_return_std_pct','peak_margin_proxy_usd']} for k,v in scenario['summaries'].items()})
    print('sensitivity',{name:{k:round(s['net_pnl'],2) for k,s in v['summaries'].items()} for name,v in sensitivities.items()})
    for trio in zip(primary['trades']['0'],primary['trades']['0.66'],primary['trades']['1.0']):
        t=trio[0];print(t['report_number'],t['ticker'],'at',t['start_utc'],'beta',round(t['beta'],3),'net',[round(z['net_pnl'],2) for z in trio],'venue',t['stock_entry_venue'],t['entry_stock_volume'],t['entry_etf_volume'])
if __name__=='__main__':main()
