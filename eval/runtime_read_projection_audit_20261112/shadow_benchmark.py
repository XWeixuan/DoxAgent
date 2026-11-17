import json,time,sqlite3,tempfile,hashlib,os,shutil
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.formal import FormalProjectors

class Hybrid(ReadStore):
 def __init__(self,target):super().__init__('/data/read/serving-v4-20261018/v2.sqlite3');self.target=target
 @contextmanager
 def connect(self,*,write=False,timeout=.2):
  assert not write
  with super().connect(write=False,timeout=timeout) as db:
   db.execute('PRAGMA query_only=ON');yield db
 def put_content(self,*a,**k):return self.target.put_content(*a,**k)
 def search_content(self,ticker,reference,body=None):return '@body:'+reference['sha256']

out={'at':datetime.now(timezone.utc).isoformat(),'note':'isolated small DB, production read-only mapper dependencies; actual contents and ingest writes; not a full-history replay or production B-tree capacity guarantee'}
with tempfile.TemporaryDirectory(prefix='dox-projection-bench-') as folder:
 assert Path(folder).resolve().parent==Path('/tmp')
 target=ReadStore(Path(folder)/'read.db');target.migrate();hybrid=Hybrid(target);mapper=FormalProjectors(hybrid,None,'/data/events')
 sources=[SourceOutbox('/data/runtime/runtime.sqlite3','runtime'),SourceOutbox('/data/bus/bus.sqlite3','bus')]
 stages={};total_start=time.perf_counter();total=0
 for source in sources:
  head=source.head()
  with source.connection() as db:coords=[r[0] for r in db.execute('SELECT seq FROM v2_source_outbox WHERE seq>? AND seq<=? ORDER BY seq LIMIT 600',(head-6000,head-5000))]
  t=time.perf_counter();cpu=time.process_time();errors={};n=0;skips=0
  with hybrid.projection_session(),target.projection_session(),source.event_reader(serving=True,projection=True) as load:
   for seq in coords:
    if time.perf_counter()-total_start>30:break
    event=load(seq);event['source']=source.source
    try:
     records,counts=mapper(event)
     target.ingest(source.source,str(seq),records,position=seq,contributions=counts,at=event['recorded_at'])
     skips+=int(bool(event.get('serving_skipped')));n+=1
    except Exception as exc:
     key=type(exc).__name__+':'+str(exc);errors[key]=errors.get(key,0)+1
  elapsed=time.perf_counter()-t;total+=n
  stages[source.source]={'events':n,'seconds':elapsed,'per_s':n/elapsed,'cpu_s':time.process_time()-cpu,'skipped':skips,'errors':errors}
 size=sum(p.stat().st_size for p in Path(folder).rglob('*') if p.is_file())
 assert size<512*1024*1024
 out.update(stages=stages,total_events=total,seconds=time.perf_counter()-total_start,scratch_bytes=size)
print(json.dumps(out))
