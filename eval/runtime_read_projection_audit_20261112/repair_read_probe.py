"""Compare existing event() and existing event_reader() on identical gap coordinates."""
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from doxagent.v2_read.outbox import SourceOutbox

d=sqlite3.connect('file:/data/read/serving-v4-20261018/v2.sqlite3?mode=ro',uri=True)
coords=[int(r[0]) for r in d.execute("SELECT event FROM gaps WHERE source='runtime' AND reason='ValueError' AND event NOT GLOB '*[^0-9]*' ORDER BY CAST(event AS INTEGER) DESC LIMIT 60")]
d.close();s=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
out={'at':datetime.now(timezone.utc).isoformat(),'coordinates':len(coords),'rounds':[]}
def digest(e):return hashlib.sha256(json.dumps(e,sort_keys=True).encode()).hexdigest()
for mode in ('per_event','reused','reused','per_event'):
    samples=[];checks=[];start=time.monotonic()
    if mode=='per_event':
        for n in coords:
            t=time.monotonic();e=s.event(n,serving=True);elapsed=time.monotonic()-t
            samples.append(elapsed*1000);checks.append(digest(e))
    else:
        with s.event_reader(serving=True) as load:
            for n in coords:
                t=time.monotonic();e=load(n);elapsed=time.monotonic()-t
                samples.append(elapsed*1000);checks.append(digest(e))
    out['rounds'].append({'mode':mode,'elapsed_ms':(time.monotonic()-start)*1000,'read_ms':samples,'digests':checks})
out['same_events']=all(r['digests']==out['rounds'][0]['digests'] for r in out['rounds'])
print(json.dumps(out))
