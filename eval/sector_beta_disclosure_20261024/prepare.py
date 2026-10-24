"""Freeze the supplied screenshot's clock and keep the prior experiment immutable."""
import json
from datetime import datetime,UTC,timedelta,time
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).parent;OLD=ROOT.parent/'sector_beta_backtest_20261023';BJ=ZoneInfo('Asia/Shanghai');ET=ZoneInfo('America/New_York')
times={14:'10-07 01:05',15:'10-07 06:28',16:'10-09 14:05',1:'10-14 19:30',2:'10-15 04:40',3:'10-15 04:40',4:'10-15 20:40',5:'10-19 17:15',6:'10-19 22:11',7:'10-19 22:11',8:'10-20 03:24',9:'10-20 11:17',10:'10-20 10:00',11:'10-20 20:32',12:'10-21 04:43',13:'10-21 18:33'}
notes={14:'用户截图：Intel/Public Technologies公开分发；17:05 UTC',15:'用户截图：新增供电观点的公开解读；机架功耗消息更早，未给更早分钟',16:'用户截图：Reuters最早可核转载；Nikkei原稿分钟未知',1:'用户截图：Kiryat Gat Net首报事故',2:'用户截图：公司/GlobeNewswire公告',3:'与#2同源同时间；原账本重复拦截',4:'用户截图：StreetInsider KeyBanc降目标价；原研报及收入下修首次完整披露分钟未核',5:'用户截图：17:15疑似泄漏；明确NF3为17:33，另做敏感性',6:'用户截图：Reuters美东10:11；Modal官方分钟未核',7:'与#6同一催化同一披露时间',8:'用户截图：Bloomberg 19:24 UTC',9:'用户截图：经济日报11:17:11北京时间，分钟重算统一取11:18',10:'用户截图：最早可核Reuters转载；工会首发分钟未核',11:'用户截图：AGI Pulse二级记录，未证实Google官方分钟',12:'用户截图：直播开始后约13分钟，即04:43；原先计划幅度表述不同，本轮仅改时钟不重判方向',13:'用户截图：NVIDIA/GlobeNewswire 06:33美东'}
rows=json.loads((OLD/'audit_combined.json').read_text(encoding='utf8'))['intents']
clock=[];needed=set()
for r in rows:
    n=r['report_number'];stamp=datetime.strptime('2026-'+times[n],'%Y-%m-%d %H:%M').replace(tzinfo=BJ)
    if n==9:stamp+=timedelta(seconds=11)
    target=stamp.replace(second=0)+(timedelta(minutes=1) if stamp.second else timedelta())
    clock.append(dict(report_number=n,intent_id=r['intent_id'],ticker=r['ticker'],direction=r['direction'],disclosure_bj=stamp.isoformat(),disclosure_utc=stamp.astimezone(UTC).isoformat(),target_entry_utc=target.astimezone(UTC).isoformat(),note=notes[n]))
    day=target.astimezone(ET).date();end=datetime.fromisoformat(r['exit_at']).astimezone(ET).date()
    hedge=json.loads((OLD/'beta.json').read_text())['mapping'][r['ticker']]
    while day<=end:
        if day.weekday()<5:
            needed.add((r['ticker'],day.isoformat()));needed.add((hedge,day.isoformat()))
        day+=timedelta(days=1)
(ROOT/'disclosure_clock.json').write_text(json.dumps(clock,ensure_ascii=False,indent=2),encoding='utf8')
for name in ['audit_combined.json','history.jsonl','beta.json','results.json']:
    (ROOT/('prior_'+name)).write_bytes((OLD/name).read_bytes())
cache={}
for name in ['cached_minutes.jsonl','minutes.jsonl']:
    for line in (OLD/name).read_text(encoding='utf8').splitlines():
        q=json.loads(line)
        if q['kind']=='bars':cache[(q['target']['ticker'],q['target']['day'])]=q
(ROOT/'cached_minutes.jsonl').write_text('\n'.join(json.dumps(q,ensure_ascii=False) for q in cache.values())+'\n',encoding='utf8')
download=[dict(ticker=s,day=d) for s,d in sorted(needed) if (s,d) not in cache]
template=(OLD/'prices_remote.py').read_text(encoding='utf8')
start=template.index('targets=');end=template.index('\net=ZoneInfo',start)
template=template[:start]+'targets='+repr(download)+template[end:]
template=template.replace('client_id=231052','client_id=241053')
(ROOT/'prices_remote.py').write_text(template,encoding='utf8')
(ROOT/'price_plan.json').write_text(json.dumps(dict(needed=sorted(needed),download=download),indent=2),encoding='utf8')
(ROOT/'run_remote.py').write_text((OLD/'run_remote.py').read_text(encoding='utf8').replace('doxagent-v2-v2-scheduler-1','doxagent-v2-v2-api-1'),encoding='utf8')
print('clock rows',len(clock),'cached day series',len(cache),'additional SMART series',download)
