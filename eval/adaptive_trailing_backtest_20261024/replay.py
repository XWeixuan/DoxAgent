"""Causal five-second quote replay. Partial downloads never imply no trigger."""
import bisect,csv,json,math,statistics
from collections import deque
from datetime import datetime,UTC,timedelta
from pathlib import Path
ROOT=Path(__file__).parent
START=int(datetime(2026,10,14,20,40,tzinfo=UTC).timestamp())
FIXED_END=int(datetime(2026,10,15,19,30,tzinfo=UTC).timestamp())
BETA=2.0236417685427837;N=10000.
PARAMS={'previous_50_2.5':(.5,2.5),'new_40_2.25':(.4,2.25)}
def ts(s):return int(datetime.fromisoformat(s).timestamp())
def iso(t):return datetime.fromtimestamp(t,UTC).isoformat()
def load():
    states={s:{} for s in ['RKLB','QQQ']};coverage={s:0 for s in states};errors=[];pages=[]
    # The downloader appends large pages; ignore only an unfinished final line.
    complete=(ROOT/'quote_pages.jsonl').read_bytes().split(b'\n')[:-1]
    for line in complete:
        q=json.loads(line)
        if q['kind']=='quote_error':errors.append(q)
        if q['kind']!='quote_page' or 'response' not in q:continue
        pages.append(q);s=q['symbol']
        for e in q['response']['events']:states[s][ts(e['timestamp'])]=e
        if not q.get('cached_probe'):coverage[s]=max(coverage[s],ts(q['response']['events'][-1]['timestamp']))
    full=ROOT/'confirmation_quotes.jsonl'
    if full.exists():
        for line in full.read_text(encoding='utf8').splitlines():
            row=json.loads(line)
            if row.get('kind')!='full_confirmation_page':continue
            for event in row['response']['events']:
                states[row['symbol']][ts(event['timestamp'])]=event
            # A targeted window never extends contiguous path coverage.
    return states,coverage,errors,pages
def valid(e,t,age=5,spread=.005):
    if not e:return False,'MISSING'
    if not 0<t-ts(e['timestamp'])<=age:return False,'STALE'
    b=float(e['bid_price']);a=float(e['ask_price'])
    if not math.isfinite(b+a) or b<=0 or a<=0 or a<b:return False,'INVALID_PRICE'
    if float(e['bid_size'])<=0 or float(e['ask_size'])<=0:return False,'ZERO_SIZE'
    if (a-b)/((a+b)/2)>spread:return False,'WIDE_SPREAD'
    return True,'VALID'
class Quotes:
    def __init__(self,states):self.states=states;self.times={s:sorted(v) for s,v in states.items()}
    def get(self,s,t):
        i=bisect.bisect_left(self.times[s],t)-1
        return self.states[s][self.times[s][i]] if i>=0 else None
def commission(q,p):return min(max(abs(q)*.005,1.),abs(q)*p*.01)
def make_position(q,hedge):
    for t in range(START,START+61,5):
        stock=q.get('RKLB',t);etf=q.get('QQQ',t)
        if valid(stock,t)[0] and valid(etf,t)[0]:
            entry=float(stock['bid_price']);eentry=float(etf['ask_price']);qs=-N/entry;qe=N*.66*BETA/eentry if hedge else 0.
            return dict(start=t,stock_entry=entry,etf_entry=eentry,qs=qs,qe=qe,hedged=hedge,entry_stock_quote=stock,entry_etf_quote=etf,entry_commission=commission(qs,entry)+(commission(qe,eentry) if hedge else 0))
    raise RuntimeError('No valid synchronized entry quote within one minute')
def valuation(pos,stock,etf,t):
    gross=pos['qs']*(float(stock['ask_price'])-pos['stock_entry'])+pos['qe']*(float(etf['bid_price'])-pos['etf_entry'])
    ec=commission(pos['qs'],float(stock['ask_price']))+(commission(pos['qe'],float(etf['bid_price'])) if pos['hedged'] else 0)
    nights=max(0,(datetime.fromtimestamp(t,UTC).date()-datetime.fromtimestamp(pos['start'],UTC).date()).days)
    # These positions start late US afternoon; UTC day difference is corrected
    # to New York calendar nights, consistent with the prior cost model.
    from zoneinfo import ZoneInfo
    zone=ZoneInfo('America/New_York');nights=max(0,(datetime.fromtimestamp(t,UTC).astimezone(zone).date()-datetime.fromtimestamp(pos['start'],UTC).astimezone(zone).date()).days)
    borrow=N*.02*nights/360
    return gross-pos['entry_commission']-ec-borrow
def warmup_atr(pos):
    bars={}
    for l in (ROOT/'warmup_and_exit.jsonl').read_text(encoding='utf8').splitlines():
        r=json.loads(l)
        if r['kind']=='warmup_5sec_quote_bars' and 'response' in r:
            bars[(r['symbol'],r['show'])]={int(b['date']):b for b in r['response']['bars']}
    values={}
    for stamp in sorted(set.intersection(*(set(v) for v in bars.values()))):
        if stamp+5>pos['start']:continue
        sb=bars[('RKLB','BID')][stamp]['close'];sa=bars[('RKLB','ASK')][stamp]['close'];eb=bars[('QQQ','BID')][stamp]['close'];ea=bars[('QQQ','ASK')][stamp]['close']
        if not(0<sb<=sa and 0<eb<=ea):continue
        if (sa-sb)/((sa+sb)/2)>.005 or pos['hedged'] and (ea-eb)/((ea+eb)/2)>.005:continue
        pnl=pos['qs']*(sa-pos['stock_entry'])+pos['qe']*(eb-pos['etf_entry'])-pos['entry_commission']-2 if pos['hedged'] else pos['qs']*(sa-pos['stock_entry'])-2
        values.setdefault(stamp//60,[]).append(pnl)
    trs=deque(maxlen=20);previous=None;valid_bars=0
    for minute,samples in sorted(values.items()):
        if (minute+1)*60>pos['start'] or len(samples)<6:continue
        high=max(samples);low=min(samples);close=samples[-1]
        if previous is not None:trs.append(max(high-low,abs(high-previous),abs(low-previous)))
        previous=close;valid_bars+=1
    return trs,previous,valid_bars
def replay_position(quotes,pos,end,coef,mult):
    history=deque();recent_valid=deque(maxlen=3);trs,previous_close,warm_bars=warmup_atr(pos);current_minute=None;bar=[]
    mfe=0.;line=None;activated=None;trigger=None;trace=[];eligible=0;invalid=0;ready_when_mfe=False
    warm=pos['start']
    for t in range(warm,end+1,5):
        # Observation at hh:mm:00 uses quotes strictly BEFORE that instant;
        # it completes the preceding minute rather than the next minute.
        minute=(t-1)//60
        if current_minute is not None and minute!=current_minute:
            if len(bar)>=6:
                hi=max(bar);lo=min(bar);close=bar[-1]
                if previous_close is not None:trs.append(max(hi-lo,abs(hi-previous_close),abs(lo-previous_close)))
                previous_close=close
            bar=[]
        current_minute=minute
        stock=quotes.get('RKLB',t);etf=quotes.get('QQQ',t);ok1,r1=valid(stock,t);ok2,r2=valid(etf,t)
        ok=ok1 and (ok2 or not pos['hedged']);pnl=valuation(pos,stock,etf,t) if ok else None
        if ok:bar.append(pnl)
        if t<pos['start']:continue
        if ok:eligible+=1
        else:invalid+=1
        atr=statistics.mean(trs) if len(trs)==20 else None
        if ok:
            recent_valid.append((t,pnl))
            if len(recent_valid)==3 and t-recent_valid[0][0]<=15:
                mfe=max(mfe,statistics.median(v for _,v in recent_valid))
        was_active=activated is not None
        if mfe>=.05*N:
            if atr is not None:
                ready_when_mfe=True;candidate=mfe-max(coef*mfe,mult*atr)
                if candidate>0:
                    if activated is None:activated=t;history.clear()
                    line=max(line,candidate) if line is not None else candidate
        if activated is not None and was_active:
            history.append((t,ok,bool(ok and pnl<=line)))
            while history and history[0][0]<=t-30:history.popleft()
            if t>=activated+30 and len(history)==6 and all(v[1] for v in history) and sum(v[2] for v in history)>=5 and history[-1][2]:
                trigger=dict(time=t,time_utc=iso(t),net_pnl=pnl,stock_ask=stock['ask_price'],etf_bid=etf['bid_price'],stop_line_net_usd=line,confirmed_breaches=sum(v[2] for v in history),observations=[dict(time_utc=iso(a),valid=b,breach=c) for a,b,c in history],stock_quote=stock,etf_quote=etf)
        trace.append(dict(timestamp=t,time_utc=iso(t),valid=ok,reject_stock=r1,reject_etf=r2,net_liquidation_pnl=pnl,mfe_net_usd=mfe,atr20_net_usd=atr,stop_line_net_usd=line,activated=activated is not None,breaches_in_window=sum(v[2] for v in history),valid_window=sum(v[1] for v in history),triggered=trigger is not None))
        if trigger:break
    return dict(position=pos,coef=coef,atr_multiplier=mult,data_through=iso(end),warmup_valid_bars=warm_bars,activation_utc=iso(activated) if activated else None,trigger=trigger,mfe_at_exit_or_coverage=mfe,atr_ready_when_5pct_reached=ready_when_mfe,valid_observations=eligible,invalid_observations=invalid,valid_fraction=eligible/(eligible+invalid) if eligible+invalid else 0,status='TRIGGERED' if trigger else 'FIXED_EXIT' if end>=FIXED_END else 'INCOMPLETE_DATA',trace=trace)
def main():
    states,coverage,errors,pages=load();end=min(coverage.values());q=Quotes(states)
    if end<START+30:
        print(json.dumps(dict(status='WARMUP_DOWNLOAD',coverage={s:iso(t) for s,t in coverage.items()},errors=errors)));return
    result=dict(coverage={s:iso(t) for s,t in coverage.items()},download_errors=errors,case2={})
    for hedge in [False,True]:
        key='beta66' if hedge else 'unhedged';pos=make_position(q,hedge);result['case2'][key]={}
        for name,(coef,mult) in PARAMS.items():
            r=replay_position(q,pos,end,coef,mult);trace=r.pop('trace')
            with (ROOT/f'trace_{key}_{name}.csv').open('w',encoding='utf8',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(trace[0]));w.writeheader();w.writerows(trace)
            result['case2'][key][name]=r
    (ROOT/'partial_results.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    for h,strategies in result['case2'].items():
        for name,r in strategies.items():print(h,name,r['status'],'activation',r['activation_utc'],'trigger',r['trigger'] and r['trigger']['time_utc'],'net',r['trigger'] and r['trigger']['net_pnl'],'MFE',round(r['mfe_at_exit_or_coverage'],2),'valid',round(r['valid_fraction'],3))
    print('coverage',result['coverage'])
if __name__=='__main__':main()
