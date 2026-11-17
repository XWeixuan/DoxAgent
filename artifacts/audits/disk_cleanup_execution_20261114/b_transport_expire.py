import json,time,sqlite3,os
from pathlib import Path
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.source_lifecycle import expire
os.nice(10);out={};base=Path('/data')
for name,path in [('research',base/'research/research.sqlite3'),('bus',base/'bus/bus.sqlite3')]:
 source=SourceOutbox(path,name);s={'expired':0,'scanned':0,'exceptions':0,'batches':0,'errors':[]};started=time.monotonic()
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:s['free_before']=db.execute('PRAGMA freelist_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0];s['consumer_before']=db.execute('SELECT * FROM v2_source_consumers').fetchall()
 while time.monotonic()-started<180:
  try:
   r=expire(source,limit=64);s['batches']+=1
   for k in ['expired','scanned','exceptions']:s[k]+=r[k]
   if not r['scanned']:break
   # A complete no-expiry traversal is not worth repeating in this one-off.
   if not r['expired'] and s['batches']>=8:break
  except Exception as e:s['errors'].append(str(e));break
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:s['free_after']=db.execute('PRAGMA freelist_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0];s['consumer_after']=db.execute('SELECT * FROM v2_source_consumers').fetchall()
 s['elapsed']=time.monotonic()-started;out[name]=s;print(json.dumps({'stage':name,**s}),flush=True)
print(json.dumps({'result':out}),flush=True)
