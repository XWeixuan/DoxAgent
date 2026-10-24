"""Offline paired-trade replay, chronological MTM risk and cost sensitivities."""
import csv,hashlib,json,math,statistics
from datetime import datetime,timedelta,UTC,time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np

ROOT=Path(__file__).parent;SOURCE=ROOT.parent/'trade_intent_return_audit_20261022';ET=ZoneInfo('America/New_York')
AUDIT=json.loads((ROOT/'prior_audit_combined.json').read_text(encoding='utf8'))['intents']
BETA=json.loads((ROOT/'prior_beta.json').read_text(encoding='utf8'));FITS={f['intent_id']:f for f in BETA['fits']}
DATA={};RAW=[]
for name in ['cached_minutes.jsonl','minutes.jsonl']:
    for line in (ROOT/name).read_text(encoding='utf8').splitlines():
        q=json.loads(line)
        if q['kind']=='bars':
            assert 'error' not in q
            symbol=q['target']['ticker']
            for b in q['response']['bars']:DATA.setdefault(symbol,{})[int(b['date'])]=b
EVENTS={1:'事故/生产风险',2:'项目进度',3:'项目进度',4:'业绩预期下修',5:'事故/生产风险',6:'竞争/性能/生态',7:'竞争/性能/生态',8:'AI/供电扩产需求',9:'供应链约束',10:'事故/生产风险',11:'竞争/性能/生态',12:'AI/供电扩产需求',13:'AI/供电扩产需求',14:'竞争/性能/生态',15:'AI/供电扩产需求',16:'下游需求下修'}
CAPITAL=100000.;NOTIONAL=10000.;LEVELS=[0,.66,1.]
def dt(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def ceilminute(t):return int((t.replace(second=0,microsecond=0)+(timedelta(minutes=1) if t.second or t.microsecond else timedelta())).timestamp())
def bar(symbol,t):return DATA.get(symbol,{}).get(t)
def last_active(symbol,t):
    candidates=[z for z,b in DATA[symbol].items() if z<=t and b['volume']>0]
    return max(candidates) if candidates else None
def commission(q,price):return min(max(abs(q)*.005,1.),abs(q)*price*.01)
def build_rows(beta_days=30,price_basis='minute',include_duplicate=False,equal_gross=False,include_missing_at_open=False):
    out=[]
    for a in AUDIT:
        if a['status']=='DUPLICATE_POLICY' and not include_duplicate:continue
        if not a['entry_tick'] and not include_missing_at_open:continue
        start=ceilminute(dt(a['entry_at']));end=int(dt(a['exit_at']).timestamp());f=FITS[a['intent_id']]
        fit=f['primary'] if beta_days==30 else f['alternates'][str(beta_days)]
        symbol=a['ticker'];hedge=fit['etf'];b=fit['beta']
        if not a['entry_tick']:
            eligible=sorted(set(DATA[symbol])&set(DATA[hedge]));start=next(t for t in eligible if start<=t<end and bar(symbol,t)['volume']>0 and bar(hedge,t)['volume']>0)
        assert bar(symbol,start) and bar(hedge,start) and bar(symbol,end) and bar(hedge,end),(symbol,start,end)
        assert dt(fit['max_bar_end_utc']).timestamp()<start
        if price_basis=='minute':p0=bar(symbol,start)['open'];p1=bar(symbol,end)['open']
        else:p0=a['entry_tick']['price'];p1=a['exit_tick']['price']
        e0=bar(hedge,start)['open'];e1=bar(hedge,end)['open'];sign=1 if a['direction']=='LONG' else -1
        row=dict(report_number=a['report_number'],intent_id=a['intent_id'],ticker=symbol,etf=hedge,direction=a['direction'],event_type=EVENTS[a['report_number']],cluster='MODAL' if a['report_number'] in [6,7] else str(a['report_number']),start=start,end=end,start_utc=datetime.fromtimestamp(start,UTC).isoformat(),end_utc=datetime.fromtimestamp(end,UTC).isoformat(),original_entry_utc=a['entry_at'],released_at=a['released_at'],beta=b,beta_days=beta_days,beta_r2=fit['r_squared'],beta_ci95=fit.get('beta_ci95_day_bootstrap'),stock_entry=p0,stock_exit=p1,etf_entry=e0,etf_exit=e1,stock_return_pct=sign*(p1/p0-1)*100,etf_raw_return_pct=(e1/e0-1)*100,audit_return_pct=float(a['return_pct']) if a['return_pct'] is not None else None,overnights=(datetime.fromtimestamp(end,UTC).astimezone(ET).date()-datetime.fromtimestamp(start,UTC).astimezone(ET).date()).days,entry_stock_bar_volume=bar(symbol,start)['volume'],entry_etf_bar_volume=bar(hedge,start)['volume'],entry_etf_prior_active_minute_age=(start-last_active(hedge,start))/60)
        assert p0>0 and p1>0 and e0>0 and e1>0
        out.append(row)
    return out

def trade(row,k,equal_gross=False,borrow_rate=.02,funding_rate=0.):
    sign=1 if row['direction']=='LONG' else -1
    n=NOTIONAL/(1+k*row['beta']) if equal_gross else NOTIONAL
    hedge_n=n*k*row['beta'];qs=sign*n/row['stock_entry'];qe=-sign*hedge_n/row['etf_entry']
    gross=qs*(row['stock_exit']-row['stock_entry'])+qe*(row['etf_exit']-row['etf_entry'])
    entry_comm=commission(qs,row['stock_entry'])+(commission(qe,row['etf_entry']) if k else 0)
    exit_comm=commission(qs,row['stock_exit'])+(commission(qe,row['etf_exit']) if k else 0)
    short_n=n if sign==-1 else hedge_n;long_n=hedge_n if sign==-1 else n
    borrow=short_n*borrow_rate*row['overnights']/360
    # Optional funding stress charges every long dollar; base account has no debit.
    funding=long_n*funding_rate*row['overnights']/360
    return dict(**row,hedge_strength=k,stock_notional=n,hedge_notional=hedge_n,stock_shares=qs,etf_shares=qe,gross_pnl=gross,entry_commission=entry_comm,exit_commission=exit_comm,commission=entry_comm+exit_comm,borrow_cost=borrow,funding_cost=funding,total_cost=entry_comm+exit_comm+borrow+funding,net_pnl=gross-entry_comm-exit_comm-borrow-funding,net_return_on_stock_pct=(gross-entry_comm-exit_comm-borrow-funding)/n*100,gross_notional=n+hedge_n)

def portfolio(trades,save=None):
    start=min(t['start'] for t in trades);end=max(t['end'] for t in trades)
    times=sorted({v for t in trades for v in [t['start'],t['end']]}|{z for symbol in DATA for z in DATA[symbol] if start<=z<=end})
    # Value both legs at the same minute open. For market-closed gaps carry
    # the most recent observable open; no future backward fill is permitted.
    price_times={s:sorted(DATA[s]) for s in DATA};import bisect
    def mark(s,t):
        idx=bisect.bisect_right(price_times[s],t)-1;assert idx>=0
        return DATA[s][price_times[s][idx]]['open']
    series=[dict(timestamp=start-1,equity=CAPITAL,gross_exposure=0.,long_cash_requirement=0.)]
    for ts in times:
        pnl=0.;gross=0.;long=0.;active=0
        for t in trades:
            if ts<t['start']:continue
            if ts>=t['end']:pnl+=t['net_pnl'];continue
            active+=1;ps=mark(t['ticker'],ts);pe=mark(t['etf'],ts)
            frac=(ts-t['start'])/(t['end']-t['start'])
            pnl+=t['stock_shares']*(ps-t['stock_entry'])+t['etf_shares']*(pe-t['etf_entry'])-t['entry_commission']-(t['borrow_cost']+t['funding_cost'])*frac
            gross+=abs(t['stock_shares']*ps)+abs(t['etf_shares']*pe)
            long+=max(t['stock_shares']*ps,0)+max(t['etf_shares']*pe,0)
        series.append(dict(timestamp=ts,equity=CAPITAL+pnl,gross_exposure=gross,long_cash_requirement=long,active_trades=active))
    eq=np.array([r['equity'] for r in series]);peak=np.maximum.accumulate(eq);dd=peak-eq;ddpct=(peak-eq)/peak*100
    # All weekdays in the sample, including no-trade days; use 20:00 ET valuation.
    day=datetime.fromtimestamp(start,UTC).astimezone(ET).date();lastday=datetime.fromtimestamp(end,UTC).astimezone(ET).date();daily=[];prev=CAPITAL
    tsarr=[r['timestamp'] for r in series]
    while day<=lastday:
        if day.weekday()<5:
            cutoff=int(datetime.combine(day,time(20),ET).timestamp());ix=bisect.bisect_right(tsarr,cutoff)-1;value=series[max(ix,0)]['equity'];daily.append(dict(day=day.isoformat(),equity=value,pnl=value-prev,return_pct=(value/prev-1)*100));prev=value
        day+=timedelta(days=1)
    vals=np.array([t['net_pnl'] for t in trades]);rets=np.array([t['net_pnl']/t['stock_notional']*100 for t in trades]);wins=vals[vals>0];losses=vals[vals<0]
    dreturns=np.array([d['return_pct'] for d in daily]);peakgross=max(r['gross_exposure'] for r in series);peaklong=max(r['long_cash_requirement'] for r in series)
    # No cross-case margin credits; compare a common conservative capital proxy.
    initial_margin_proxy=.5*peakgross
    out=dict(trades=len(trades),gross_pnl=float(sum(t['gross_pnl'] for t in trades)),commissions=float(sum(t['commission'] for t in trades)),borrow_cost=float(sum(t['borrow_cost'] for t in trades)),funding_cost=float(sum(t['funding_cost'] for t in trades)),total_cost=float(sum(t['total_cost'] for t in trades)),net_pnl=float(vals.sum()),account_gross_return_pct=float(sum(t['gross_pnl'] for t in trades)/CAPITAL*100),account_net_return_pct=float(vals.sum()/CAPITAL*100),win_rate_pct=float(len(wins)/len(vals)*100),wins=len(wins),losses=len(losses),average_win_usd=float(wins.mean()) if len(wins) else None,average_loss_usd=float(abs(losses.mean())) if len(losses) else None,payoff_ratio=float(wins.mean()/abs(losses.mean())) if len(wins) and len(losses) else None,profit_factor=float(wins.sum()/abs(losses.sum())) if len(losses) else None,trade_return_std_pct=float(rets.std(ddof=1)) if len(rets)>1 else 0.,maximum_drawdown_usd=float(dd.max()),maximum_drawdown_pct=float(ddpct.max()),daily_return_std_pct=float(dreturns.std(ddof=1)) if len(dreturns)>1 else 0.,daily_mean_std_ratio=float(dreturns.mean()/dreturns.std(ddof=1)) if len(dreturns)>1 and dreturns.std(ddof=1)>0 else None,daily_observations=len(daily),peak_gross_exposure_usd=peakgross,peak_margin_proxy_usd=initial_margin_proxy,peak_long_cash_requirement_usd=peaklong,financing_debit_required=max(0,peaklong-CAPITAL),net_pnl_per_peak_margin_pct=float(vals.sum()/initial_margin_proxy*100),net_pnl_per_10000_gross_turnover=float(vals.sum()/sum(2*t['gross_notional'] for t in trades)*10000),gross_notional_days=sum(t['gross_notional']*(t['end']-t['start'])/86400 for t in trades),daily=daily)
    assert abs(series[-1]['equity']-CAPITAL-out['net_pnl'])<1e-7
    if save:
        with save.open('w',encoding='utf8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(series[1]));w.writeheader();w.writerows(series[1:])
    return out

def run(beta_days=30,price_basis='minute',include_duplicate=False,equal_gross=False,borrow_rate=.02,funding_rate=0.,include_missing_at_open=False,omit_numbers=()):
    rows=build_rows(beta_days,price_basis,include_duplicate,equal_gross,include_missing_at_open);rows=[r for r in rows if r['report_number'] not in omit_numbers]
    summaries={};trades={}
    for k in LEVELS:
        key=str(k);trades[key]=[trade(r,k,equal_gross,borrow_rate,funding_rate) for r in rows];summaries[key]=portfolio(trades[key])
    return dict(summaries=summaries,trades=trades)

def main():
    primary=run();assert len(primary['trades']['0'])==14
    for k in LEVELS:portfolio(primary['trades'][str(k)],ROOT/f'equity_{k}.csv')
    sensitivities={
      'beta20':run(beta_days=20),'beta60':run(beta_days=60),
      'same_gross_10000':run(equal_gross=True),
      'include_blocked_duplicate':run(include_duplicate=True),
      'original_stock_tick_proxy':run(price_basis='audit_tick'),
      'drop_second_modal_case':run(omit_numbers=(7,)),
      'drop_all_intc':run(omit_numbers=tuple(r['report_number'] for r in AUDIT if r['ticker']=='INTC')),
      'borrow10pct_and_long_funding5pct':run(borrow_rate=.10,funding_rate=.05),
      'missing_case_next_common_open':run(include_missing_at_open=True),
      'intraday_only':run(omit_numbers=tuple(r['report_number'] for r in build_rows() if r['overnights']>0)),
    }
    attribution={}
    for dim in ['ticker','direction','event_type']:
        attribution[dim]={}
        groups=sorted({t[dim] for t in primary['trades']['0']})
        for group in groups:
            attribution[dim][group]={}
            for k,ts in primary['trades'].items():
                subset=[t for t in ts if t[dim]==group];vals=[t['net_pnl'] for t in subset]
                attribution[dim][group][k]=dict(count=len(subset),net_pnl=sum(vals),gross_pnl=sum(t['gross_pnl'] for t in subset),wins=sum(v>0 for v in vals),net_return_std_pct=statistics.stdev([v/NOTIONAL*100 for v in vals]) if len(vals)>1 else None)
    # Cluster by ET entry day; this keeps same-day correlated messages together.
    dates=sorted({datetime.fromtimestamp(t['start'],UTC).astimezone(ET).date().isoformat() for t in primary['trades']['0']})
    rng=np.random.default_rng(231023);bootstrap={}
    for k in [.66,1.]:
        diffs={d:0. for d in dates};levels={d:0. for d in dates}
        for a,b in zip(primary['trades']['0'],primary['trades'][str(k)]):
            d=datetime.fromtimestamp(a['start'],UTC).astimezone(ET).date().isoformat();diffs[d]+=b['net_pnl']-a['net_pnl'];levels[d]+=b['net_pnl']
        vec=np.array([diffs[d] for d in dates]);lev=np.array([levels[d] for d in dates]);ix=rng.integers(0,len(dates),size=(10000,len(dates)))
        samples=vec[ix].sum(axis=1);net=lev[ix].sum(axis=1)
        bootstrap[str(k)]=dict(entry_day_clusters=len(dates),days=dates,incremental_pnl_ci95=[float(v) for v in np.quantile(samples,[.025,.975])],net_pnl_ci95=[float(v) for v in np.quantile(net,[.025,.975])],fraction_incremental_above_zero=float((samples>0).mean()))
    result=dict(config=dict(initial_equity_usd=CAPITAL,stock_notional_per_case_usd=NOTIONAL,commission_per_share=.005,commission_minimum_per_order_usd=1,commission_cap_fraction=.01,borrow_annual_rate=.02,base_debit_interest=0,borrow_day_count=360,margin_proxy_gross_fraction=.5,primary_beta_sessions=30,bar_size='30 mins RTH',price_basis='common ceil-minute open both legs, common exit-minute open',excluded_duplicate=3,excluded_no_entry_price=9),primary=primary,sensitivities=sensitivities,attribution=attribution,bootstrap=bootstrap)
    (ROOT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    with (ROOT/'trades.csv').open('w',encoding='utf-8-sig',newline='') as f:
        flat=[t for ts in primary['trades'].values() for t in ts];w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
    for k,s in primary['summaries'].items():print(k,{key:round(v,4) if isinstance(v,float) else v for key,v in s.items() if key!='daily'})
    print('bootstrap',bootstrap)
    print('sensitivity net pnl',{name:{k:round(s['net_pnl'],2) for k,s in v['summaries'].items()} for name,v in sensitivities.items()})



# Explicit overnight venue prices are separate from SMART session prices.
VENUES={}
for symbol,data in DATA.items():
    for ts in data:VENUES[(symbol,ts)]='SMART'
for name in ['overnight.jsonl','overnight_paths.jsonl']:
    for line in (ROOT/name).read_text(encoding='utf8').splitlines():
        q=json.loads(line)
        if q['kind']=='overnight_bars' and 'response' in q:
            symbol=q['target']['symbol']
            for item in q['response']['bars']:
                ts=int(item['date'])
                if ts not in DATA.setdefault(symbol,{}):
                    DATA[symbol][ts]=item;VENUES[(symbol,ts)]='OVERNIGHT'
