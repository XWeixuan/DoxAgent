import json,time,sqlite3,os
from pathlib import Path
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.source_lifecycle import expire
os.nice(10);p=Path('/data/bus/bus.sqlite3');source=SourceOutbox(p,'bus');out={'expired':0,'scanned':0,'exceptions':0,'batches':0,'errors':[]}
with sqlite3.connect(p,timeout=3) as db:
 out['free_before']=db.execute('PRAGMA freelist_count').fetchone()[0]*4096;out['oldest_before']=db.execute('SELECT seq,recorded_at FROM v2_source_outbox ORDER BY seq LIMIT 1').fetchone();db.execute("UPDATE v2_transport_gc_state SET cursor=0 WHERE kind='v2_source_outbox'");db.commit()
started=time.monotonic()
while time.monotonic()-started<120:
 try:
  r=expire(source,limit=500);out['batches']+=1
  for k in ['expired','scanned','exceptions']:out[k]+=r[k]
  if not r['scanned'] or not r['expired']:break
  time.sleep(.01)
 except Exception as e:out['errors'].append(str(e));break
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:out['free_after']=db.execute('PRAGMA freelist_count').fetchone()[0]*4096;out['oldest_after']=db.execute('SELECT seq,recorded_at FROM v2_source_outbox ORDER BY seq LIMIT 1').fetchone()
out['elapsed']=time.monotonic()-started;print(json.dumps(out),flush=True)
