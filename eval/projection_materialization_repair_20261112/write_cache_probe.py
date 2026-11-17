"""Profile mapping with production reads; suppress content registration and all writes."""
import cProfile
import hashlib
import io
import json
import pstats
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.content_files import ContentFiles

def forbidden(*args,**kwargs):raise RuntimeError('DIAGNOSTIC_WRITE_FORBIDDEN')
ContentFiles.put=forbidden
class ReadOnlyStore(ReadStore):
    def __init__(self,path):
        super().__init__(path)
        self.content_calls=[]
    @contextmanager
    def connect(self,*,write=False,timeout=.2):
        if write:raise RuntimeError('DIAGNOSTIC_WRITE_FORBIDDEN')
        with super().connect(write=False,timeout=timeout) as db:
            db.execute('PRAGMA query_only=ON')
            deadline=time.monotonic()+2
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            yield db
    def put_content(self,ticker,body,kind='text/plain'):
        raw=body.encode(); sha=hashlib.sha256(raw).hexdigest()
        identity=hashlib.sha256(f'{ticker}:{kind}:{sha}'.encode()).hexdigest()
        self.content_calls.append({'bytes':len(raw),'sha256':sha})
        return {'content_id':identity,'content_type':kind,'size_bytes':len(raw),'sha256':sha}
    def search_content(self,ticker,reference,body=None):return '@body:'+reference['sha256']




from doxagent.v2_read.artifacts import PublishedArtifacts
import tempfile,shutil,copy,os,statistics
os.umask(0o077)
store=ReadOnlyStore('/data/read/serving-v4-20261018/v2.sqlite3');source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
e=source.event(4540033,serving=True,projection=True);e['source']='runtime'
with store.connect() as db:before=db.execute("SELECT seq FROM commits WHERE source='runtime' AND source_event='4540033'").fetchone()[0]-1
original=store.get
store.get=lambda kind,*args,**kwargs: original(kind,*args,seq=before) if kind in {'activation','event_effective_snapshot','native:v2_ticker_control'} else original(kind,*args,**kwargs)
with store.projection_session():records,counts=FormalProjectors(store,PublishedArtifacts('/data/research/research.sqlite3'),'/data/events')(e)
records=[r for r in records if r.get('data') is not None and not r['kind'].startswith('native:')]
class FixtureStore(ReadStore):
    @contextmanager
    def connect(self,*,write=False,timeout=2):
        with super().connect(write=write,timeout=timeout) as db:
            if write:db.execute('PRAGMA cache_size=-'+str(self.cache_kib))
            yield db
work=Path(tempfile.mkdtemp(prefix='dox-projection-cache-probe-'));out=[]
try:
 for cache in [2048,16384]:
  s=FixtureStore(work/str(cache)/'read.db');s.cache_kib=cache;s.migrate()
  fillers=[]
  for n in range(2500):
   r=copy.deepcopy(records[n%len(records)]);r['id']='probe-'+str(n);fillers.append(r)
  with s.projection_session():
   s.ingest('fixture','seed',records+fillers)
   times=[];cpu=[]
   for n in range(3):
    batch=copy.deepcopy(records)
    for r in batch:r['data']['_cache_probe_revision']=n
    t=time.perf_counter();c=time.process_time();s.ingest('probe',str(n),batch,position=n+1);times.append(time.perf_counter()-t);cpu.append(time.process_time()-c)
  size=sum(p.stat().st_size for p in work.rglob('*') if p.is_file())
  out.append(dict(cache_kib=cache,records=len(records),times=times,cpu=cpu,total_fixture_bytes=size))
  if size>128*1024*1024:break
 print(json.dumps(out),flush=True)
finally:
 assert work.resolve().parent==Path('/tmp') and work.name.startswith('dox-projection-cache-probe-')
 shutil.rmtree(work)
