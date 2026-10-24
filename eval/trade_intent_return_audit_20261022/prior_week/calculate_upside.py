"""Simple same-day favorable excursion and direction-matched SPY comparison."""
import hashlib,json,statistics
from datetime import datetime,UTC,timedelta,time
from pathlib import Path
from zoneinfo import ZoneInfo

root=Path(__file__).parent;et=ZoneInfo('America/New_York')
rows=json.loads((root/'results.json').read_text(encoding='utf8'))
raw=[json.loads(l) for l in (root/'upside_market.jsonl').read_text(encoding='utf8').splitlines()]
queries={ (q['target']['ticker'],q['target']['day']):q for q in raw if q['kind']=='bars'}
assert len(queries)==6 and not any('error' in q for q in queries.values())
def bars(ticker,day):
    return {int(b['date']):b for b in queries[(ticker,day)]['response']['bars'] if b['volume']>0 and b['bar_count']>0 and b['low']>0}
out=[]
for n,r in enumerate(rows,1):
    a=datetime.fromisoformat(r['entry_at']);day=a.astimezone(et).date().isoformat()
    v=dict(number=n,intent_id=r['intent_id'],ticker=r['ticker'],direction=r['direction'],entry_at=r['entry_at'],day_et=day)
    if not r['entry_tick']:
        out.append(dict(**v,status='NO_CONTEMPORANEOUS_ENTRY'));continue
    # Preserve the original entry tick price. Exclude its incomplete minute:
    # a high/low in that minute might have occurred before the entry.
    actual=datetime.fromisoformat(r['entry_tick']['timestamp'])
    start=actual.replace(second=0,microsecond=0)+(timedelta(minutes=1) if actual.second or actual.microsecond else timedelta())
    finish=datetime.combine(datetime.fromisoformat(day).date(),time(20),et).astimezone(UTC)
    stock=bars(r['ticker'],day);spy=bars('SPY',day)
    eligible={t:b for t,b in stock.items() if start.timestamp()<=t<finish.timestamp()}
    assert eligible and int(start.timestamp()) in spy
    sign=1 if r['direction']=='LONG' else -1;p0=float(r['entry_tick']['price'])
    field='high' if sign==1 else 'low'
    peak=min(eligible,key=lambda t:(-sign*eligible[t][field],t))
    assert peak in spy
    px=float(eligible[peak][field]);mfe=sign*(px/p0-1)*100
    b0=spy[int(start.timestamp())]['open'];b1=spy[peak]['close']
    spyreturn=(b1/b0-1)*100
    common=sorted(set(eligible)&set(spy));last=common[-1]
    endret=sign*(eligible[last]['close']/p0-1)*100
    endspy=(spy[last]['close']/b0-1)*100
    v.update(status='PRICED',entry_price=p0,entry_tick_at=r['entry_tick']['timestamp'],full_minute_start=start.isoformat(),minute_count=len(eligible),peak_minute=datetime.fromtimestamp(peak,UTC).isoformat(),extreme_price=px,maximum_favorable_return_pct=max(0,mfe),spy_return_to_peak_pct=spyreturn,direction_matched_spy_return_pct=sign*spyreturn,excess_at_peak_pp=mfe-sign*spyreturn,end_minute=datetime.fromtimestamp(last,UTC).isoformat(),end_price=eligible[last]['close'],end_return_pct=endret,spy_return_to_end_pct=endspy,excess_at_end_pp=endret-sign*endspy)
    assert mfe>=0 # all priced cases happen to have some favorable excursion
    assert start.timestamp()<=peak<finish.timestamp()
    out.append(v)
priced=[v for v in out if v['status']=='PRICED']
summary=dict(count=len(out),priced=len(priced),unpriced=len(out)-len(priced),average_maximum_favorable_return_pct=statistics.mean(v['maximum_favorable_return_pct'] for v in priced),median_maximum_favorable_return_pct=statistics.median(v['maximum_favorable_return_pct'] for v in priced),positive_excess_at_peak=sum(v['excess_at_peak_pp']>0 for v in priced),positive_excess_at_end=sum(v['excess_at_end_pp']>0 for v in priced),average_excess_at_peak_pp=statistics.mean(v['excess_at_peak_pp'] for v in priced),average_excess_at_end_pp=statistics.mean(v['excess_at_end_pp'] for v in priced))
(root/'upside_results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
(root/'upside_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
(root/'upside_sha256.json').write_text(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in ['upside_market.jsonl','upside_results.json','upside_summary.json']},indent=2),encoding='utf8')
print(json.dumps(summary,indent=2))
for v in out:
    print(v['number'],v['ticker'],*(f'{v[k]:.3f}' if isinstance(v.get(k),float) else str(v.get(k)) for k in ['maximum_favorable_return_pct','peak_minute','spy_return_to_peak_pct','excess_at_peak_pp','end_return_pct','excess_at_end_pp']))
