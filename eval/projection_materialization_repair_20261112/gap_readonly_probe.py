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
import traceback
store=ReadOnlyStore('/data/read/serving-v4-20261018/v2.sqlite3');source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
with store.connect() as db:
 rows=[dict(r) for r in db.execute("SELECT event,reason,attempts,next_attempt_at FROM gaps WHERE source='runtime' ORDER BY event LIMIT 2")]
 rows += [dict(r) for r in db.execute("SELECT event,reason,attempts,next_attempt_at FROM gaps WHERE source='runtime' ORDER BY event DESC LIMIT 2")]
mapper=FormalProjectors(store,PublishedArtifacts('/data/research/research.sqlite3'),'/data/events');out=[]
for row in rows:
 seq=int(row['event']);t=time.perf_counter();item=dict(row)
 try:
  event=source.event(seq,serving=True);item['receipt_available']=event is not None
  if event:
   event['source']='runtime';item['table']=event['table_name'];item['namespace']=event.get('row',{}).get('namespace')
   with store.projection_session():records,counts=mapper(event)
   item['current_map_records']=len(records)
 except Exception as exc:
  item['current_error']=type(exc).__name__
  text=str(exc)
  item['error_digest']=hashlib.sha256(text.encode()).hexdigest()
  if text.startswith(('CaseSummary/','Reference','Policy','Event','Delta','BIRTH_','PROVENANCE_')):item['safe_reason']=text.splitlines()[0][:160]
  item['frames']=[f.name for f in traceback.extract_tb(exc.__traceback__)[-5:]]
 item['seconds']=time.perf_counter()-t;out.append(item)
print(json.dumps(out))
