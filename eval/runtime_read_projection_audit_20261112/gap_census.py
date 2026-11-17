import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone

out={'at':datetime.now(timezone.utc).isoformat()}
d=sqlite3.connect('file:/data/read/serving-v4-20261018/v2.sqlite3?mode=ro',uri=True,timeout=.2)
d.row_factory=sqlite3.Row;d.execute('PRAGMA query_only=ON')
gaps=[dict(r) for r in d.execute("SELECT source,event,reason,attempts,next_attempt_at FROM gaps ORDER BY source,event")]
d.close()
s=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True,timeout=.2)
s.row_factory=sqlite3.Row;s.execute('PRAGMA query_only=ON')
counts=Counter();times=[];samples=[]
started=time.monotonic()
for i,g in enumerate(gaps):
    if time.monotonic()-started>8:break
    if g['source']!='runtime' or not g['event'].isdigit():continue
    r=s.execute('SELECT table_name,recorded_at FROM v2_source_outbox WHERE seq=?',(int(g['event']),)).fetchone()
    counts[(g['reason'],r['table_name'] if r else 'not_hot')]+=1
    if r:times.append(r['recorded_at'])
    if i%1000==0 or i>len(gaps)-5:samples.append({**g,**(dict(r) if r else {})})
out.update(total=len(gaps),examined=i+1,elapsed_s=time.monotonic()-started,groups=[{'reason':k[0],'table':k[1],'n':v} for k,v in counts.items()],earliest=min(times) if times else None,latest=max(times) if times else None,samples=samples)
print(json.dumps(out))
