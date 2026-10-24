import json
from datetime import datetime,timedelta,UTC,time
from pathlib import Path
from zoneinfo import ZoneInfo
root=Path(__file__).parent;source=root.parent/'trade_intent_return_audit_20261022';et=ZoneInfo('America/New_York')
rows=json.loads((source/'combined_results.json').read_text(encoding='utf8'))['intents'];mapping=json.loads((root/'beta.json').read_text(encoding='utf8'))['mapping']
cached={}
for file in [source/'upside_market.jsonl',source/'prior_week/upside_market.jsonl']:
    for line in file.read_text(encoding='utf8').splitlines():
        r=json.loads(line)
        if r['kind']=='bars' and 'response' in r:cached[(r['target']['ticker'],r['target']['day'])]=dict(r,cache_source=str(file.resolve()))
needed=set()
for r in rows:
    day=datetime.fromisoformat(r['entry_at']).astimezone(et).date();end=datetime.fromisoformat(r['exit_at']).astimezone(et).date()
    while day<=end:
        if day.weekday()<5:
            for symbol in [r['ticker'],mapping[r['ticker']]]:needed.add((symbol,day.isoformat()))
        day+=timedelta(days=1)
used=[cached[k] for k in sorted(needed) if k in cached]
(root/'cached_minutes.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in used)+'\n',encoding='utf8')
targets=[dict(ticker=t,day=d) for t,d in sorted(needed) if (t,d) not in cached]
template=(source/'upside_remote_template.py').read_text(encoding='utf8').replace('client_id=221043','client_id=231052')
(root/'prices_remote.py').write_text(template.replace('__TARGETS__',repr(targets)),encoding='utf8')
(root/'price_plan.json').write_text(json.dumps(dict(needed=sorted(needed),cached=len(used),download=targets),indent=2),encoding='utf8')
print('total stock/ETF day series',len(needed),'cached',len(used),'new',len(targets))
