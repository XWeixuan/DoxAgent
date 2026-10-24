import json
from datetime import datetime, timedelta, time, UTC
from zoneinfo import ZoneInfo
from pathlib import Path
root=Path(__file__).parent
x=json.loads((root/'snapshot.json').read_text(encoding='utf8'))
et=ZoneInfo('America/New_York')
def dt(t): return datetime.fromisoformat(t.replace('Z','+00:00'))
def exit_at(t):
    t=t.astimezone(et); day=t.date()
    if t.time()>=time(15,30): day+=timedelta(days=1)
    while day.weekday()>=5: day+=timedelta(days=1)
    return datetime.combine(day,time(15,30),et).astimezone(UTC)
rows=[]; targets={}
for r in sorted(x['intents'],key=lambda r:r['intent']['released_at']):
    i=r['intent'];c=r['case'];released=dt(i['released_at']);origin=dt(c['created_at'])
    entry=min(released,origin+timedelta(minutes=5));exit=exit_at(entry)
    row=dict(intent_id=i['intent_id'],ticker=i['ticker'],direction=i['trade']['decision'],status=i['status'],origin=i['trade'].get('decision_origin'),case_created_at=origin.isoformat(),released_at=released.isoformat(),latency_minutes=(released-origin).total_seconds()/60,capped=entry<released,entry_at=entry.isoformat(),exit_at=exit.isoformat(),raw_exit_at=exit_at(released).isoformat(),title=i['trade'].get('source',{}).get('snapshot',{}).get('title'),entry_result=(r.get('execution') or {}).get('entry_result'))
    for kind,t in [('entry',entry),('raw_entry',released),('exit',exit),('raw_exit',exit_at(released))]:
        key=i['ticker']+'|'+t.isoformat()+'|'+('exit' if 'exit' in kind else 'entry')
        row[kind+'_key']=key
        if t<=dt(x['captured_at']): targets[key]=dict(key=key,ticker=i['ticker'],at=t.isoformat(),kind='exit' if 'exit' in kind else 'entry')
    rows.append(row)
plan=dict(snapshot_at=x['captured_at'],rows=rows,targets=list(targets.values()))
(root/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
template=(root/'market_remote_template.py').read_text(encoding='utf8')
(root/'market_remote.py').write_text(template.replace('__PLAN__',repr(plan)),encoding='utf8')
print('intents',len(rows),'capped',sum(r['capped'] for r in rows),'market queries',len(targets))
