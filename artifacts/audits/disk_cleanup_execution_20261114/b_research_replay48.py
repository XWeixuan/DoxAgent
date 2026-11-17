import json,time,sqlite3,inspect,os
from pathlib import Path
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read import source_lifecycle as lifecycle
os.nice(10);code=inspect.getsource(lifecycle.expire);assert code.count('before=now-timedelta(days=7)')==1;scope=dict(lifecycle.__dict__);exec(code.replace('before=now-timedelta(days=7)','before=now-timedelta(hours=48)'),scope);expire48=scope['expire'];p=Path('/data/research/research.sqlite3');source=SourceOutbox(p,'research');out={'policy':'one-off acknowledged noncanonical replay older48h; current domain rows retained','expired':0,'scanned':0,'exceptions':0,'batches':0,'errors':[]}
with sqlite3.connect(p,timeout=3) as db:
 out['free_before']=db.execute('PRAGMA freelist_count').fetchone()[0]*4096;out['consumers']=db.execute('SELECT * FROM v2_source_consumers').fetchall();out['pins']=db.execute('SELECT * FROM v2_source_pins').fetchall();out['oldest_before']=db.execute('SELECT seq,recorded_at FROM v2_source_outbox ORDER BY seq LIMIT 1').fetchone();db.execute('UPDATE v2_transport_gc_state SET cursor=0');db.commit()
started=time.monotonic()
while time.monotonic()-started<360:
 try:
  r=expire48(source,limit=128);out['batches']+=1
  for k in ['expired','scanned','exceptions']:out[k]+=r[k]
  if not r['scanned'] or not r['expired']:break
  if out['batches']%20==0:print(json.dumps({'stage':'progress','expired':out['expired'],'batches':out['batches']}),flush=True)
  time.sleep(.01)
 except Exception as e:out['errors'].append(str(e));break
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 out['free_after']=db.execute('PRAGMA freelist_count').fetchone()[0]*4096;out['oldest_after']=db.execute('SELECT seq,recorded_at FROM v2_source_outbox ORDER BY seq LIMIT 1').fetchone();out['expiry_floors']=db.execute('SELECT * FROM v2_transport_gc_state').fetchall()
out['elapsed']=time.monotonic()-started;print(json.dumps({'result':out}),flush=True)
