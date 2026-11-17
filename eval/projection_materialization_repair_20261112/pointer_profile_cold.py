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
store=ReadOnlyStore('/data/read/serving-v4-20261018/v2.sqlite3')
source=SourceOutbox('/data/initialization/control.sqlite3','initialization')
e=source.event(157494,serving=True);e['source']='initialization'
mapper=FormalProjectors(store,PublishedArtifacts('/data/research/research.sqlite3'),'/data/events')

with store.connect() as db: before=db.execute("SELECT seq FROM commits WHERE source='initialization' AND source_event='157494'").fetchone()[0]-1
original=store.get
store.get=lambda kind,*args,**kwargs: original(kind,*args,seq=before) if kind in {'activation','event_effective_snapshot'} else original(kind,*args,**kwargs)
prof=cProfile.Profile()
with store.projection_session():
 t=time.perf_counter();prof.enable();records,counts=mapper(e);prof.disable();elapsed=time.perf_counter()-t
s=io.StringIO();pstats.Stats(prof,stream=s).sort_stats('cumulative').print_stats(22)
print(json.dumps(dict(seconds=elapsed,records=len(records),kinds=dict(Counter(r['kind'] for r in records)),profile=s.getvalue())))
