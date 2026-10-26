"""Reconcile complete and staged windows with their full expected denominators."""
import csv
import gzip
import json
from pathlib import Path
from collections import defaultdict
from doxagent.message_bus_v2.cadence import cadence,instant,distribution

root=Path(__file__).parent
raw=root/'window-final.json'
d=json.loads(raw.read_text(encoding='utf-8-sig')) if raw.exists() else json.loads(gzip.decompress((root/'window-final.metadata.json.gz').read_bytes()))
closed=json.loads((root/'control-closed.json').read_text(encoding='utf-8-sig'))['changed_at']
opened=json.loads((root/'live-window.json').read_text(encoding='utf-8-sig'))['control']['changed_at']
windows={'complete':(opened,closed),'after_admission':('2026-10-26T00:34:00+00:00','2026-10-26T00:42:00+00:00'),
         'nine_at_30s':('2026-10-26T00:47:30+00:00','2026-10-26T01:05:14+00:00'),
         'last_normal':('2026-10-26T01:12:00+00:00',closed)}
out={}
for name,(begin,end) in windows.items():
    captured=[c for c in d['captures'] if instant(c['observed_at'])<=instant(end)]
    r=cadence(d['runs']+d['schedules'],captured,instant(begin),instant(end))
    sources=defaultdict(lambda:defaultdict(int))
    for u in r['units']:
        s=sources[u['binding_id'].split(':',1)[-1]]
        for k in ('expected_cycles','fresh_cycles','pending_cycles','missing_cycles','request_cycles','settled_cycles'):s[k]+=u[k]
    sources={k:{**v,'all_cycle_coverage':v['fresh_cycles']/v['expected_cycles'] if v['expected_cycles'] else None,
        'settled_coverage':v['fresh_cycles']/v['settled_cycles'] if v['settled_cycles'] else None} for k,v in sources.items()}
    caps=[c for c in captured if instant(begin)<=instant(c['observed_at'])]
    r.update(since=begin,until=end,sources=sources,capture_commit_seconds=distribution([(instant(c['committed_at'])-instant(c['observed_at'])).total_seconds() for c in caps]))
    out[name]=r
    print(name,r['expected_cycles'],r['fresh_cycles'],r['pending_cycles'],round(100*r['all_cycle_coverage'],2),r['capture_commit_seconds'])
    print({k:sources[k] for k in ('barrons_ticker_news','investorshub_ticker_news','investing_ticker_news','yahoo_finance_news','reuters_site_search')})
(root/'final-metrics.json').write_text(json.dumps({k:{field:value for field,value in v.items() if field!='units'} for k,v in out.items()},ensure_ascii=False,indent=2),encoding='utf-8')
with (root/'final-coverage-units.csv').open('w',newline='',encoding='utf-8-sig') as file:
    fields=['binding_id','unit','expected_cycles','dispatched_cycles','request_cycles','fresh_cycles','pending_cycles','missing_cycles','all_cycle_coverage','coverage']
    writer=csv.DictWriter(file,fields);writer.writeheader()
    writer.writerows({k:u[k] for k in fields} for u in out['complete']['units'])
