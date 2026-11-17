"""Offline sizing of the implemented cache; frozen data stays local/ignored."""
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from doxagent.message_bus_v2.advanced_deduplication import AdvancedDeduplication, features
from doxagent.message_bus_v2.manifests import initial_sources

root = Path(__file__).resolve().parents[2]
rows = [json.loads(s) for s in (root/'.tmp-codex/message_efficiency_followup_20261117/published_content.jsonl').read_text(encoding='utf-8').splitlines()][1:]
rows.sort(key=lambda r:(r['stream_published_at'],r['ticker'],r['id']))
sources = {s.source_id:s for s in initial_sources()}
clock = [0.0]
engine = AdvancedDeduplication(clock=lambda:clock[0])
durations=[]
hits=Counter()
peak_records=peak_body=peak_header=0
for row in rows:
    source=sources.get(row['source_id'])
    if not source or not source.advanced_dedup_enabled:
        continue
    clock[0]=datetime.fromisoformat(row['stream_published_at'].replace('Z','+00:00')).timestamp()
    message=SimpleNamespace(ticker=row['ticker'],url=row['url'],title=row['title'],body=row['body'],
        publisher_name=None,raw_payload={},metadata={'language':row['language'],
        'media_enrichment':{'outcome':row['outcome']},'content_evidence':{'kind':row['body_kind']}})
    start=time.perf_counter()
    f=features(message,source)
    hit,_=engine.check(f)
    if hit:hits[hit[0]]+=1
    else:engine.remember(f,row['id'],clock[0])
    durations.append((time.perf_counter()-start)*1000)
    peak_records=max(peak_records,len(engine.records))
    peak_body=max(peak_body,engine.body_bytes)
    peak_header=max(peak_header,engine.header_bytes)
durations.sort()
result={'scope':'Local implemented module incl. normalization/admission/expiry; 20ms budget; reconstructed default source scopes; Google suffix not stripped without verified publisher metadata; no SQL or server SLA.',
    'evaluated':len(durations),'hits':dict(hits),'removed':sum(hits.values()),
    'latency_ms':{str(q):round(durations[int((len(durations)-1)*q)],4) for q in (.5,.95,.99,1)},
    'peak_records':peak_records,'peak_body_bytes':peak_body,'peak_header_estimate_bytes':peak_header,
    'metrics':dict(engine.metrics)}
(Path(__file__).parent/'implementation-benchmark.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
