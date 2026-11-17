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

import signal
def expired(*_):raise TimeoutError('DIAGNOSTIC_TOTAL_BUDGET')
signal.signal(signal.SIGALRM,expired);signal.alarm(55)
store=ReadOnlyStore('/data/read/serving-v4-20261018/v2.sqlite3')
source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
from doxagent.v2_read.artifacts import PublishedArtifacts
mapper=FormalProjectors(store,PublishedArtifacts('/data/research/research.sqlite3'),'/data/events')
out={'at':datetime.now(timezone.utc).isoformat(),'samples':[],'notes':'No ingest. put_content hashes only; writes/files suppressed. Read state is current, not historical replay.'}
with store.connect() as db:
    cp=db.execute("SELECT position FROM checkpoints WHERE source='runtime'").fetchone()[0]
    gaps=[int(r[0]) for r in db.execute("SELECT event FROM gaps WHERE source='runtime' AND reason='ValueError' AND event NOT GLOB '*[^0-9]*' ORDER BY CAST(event AS INTEGER) DESC LIMIT 8")]
with source.connection() as db:
    head=source.head()
    coords=[r[0] for r in db.execute("SELECT seq FROM v2_source_outbox WHERE seq>? AND table_name IN ('runtime_v2_cases','runtime_v2_turns','runtime_v2_w3_cases','runtime_values','runtime_tasks','runtime_v2_archives','runtime_v2_candidates','v2_analysis_admission') ORDER BY seq LIMIT 18",(cp,))]
coords=list(dict.fromkeys(coords))
profile=cProfile.Profile()
started=time.monotonic()
with store.projection_session(),source.event_reader(serving=True,projection=True) as load:
    for coordinate in coords:
        if time.monotonic()-started>50:break
        t=time.monotonic(); event=load(coordinate); decode_ms=(time.monotonic()-t)*1000
        event['source']='runtime'
        v={'seq':coordinate,'table':event['table_name'],'read_ms':decode_ms,'skipped':event.get('serving_skipped',False)}
        old=len(store.content_calls); t=time.monotonic()
        try:
            profile.enable(); records,counts=mapper(event);profile.disable()
            v.update(records=dict(Counter(r['kind'] for r in records)),contributions=len(counts))
        except Exception as exc:
            profile.disable(); v['error']=type(exc).__name__+':'+str(exc)
        v['map_ms']=(time.monotonic()-t)*1000
        v['content_calls']=store.content_calls[old:]
        out['samples'].append(v)
stats=io.StringIO();pstats.Stats(profile,stream=stats).strip_dirs().sort_stats('cumulative').print_stats(55)
out['profile_cumulative']=stats.getvalue()
stats=io.StringIO();pstats.Stats(profile,stream=stats).strip_dirs().sort_stats('tottime').print_stats(40)
out['profile_self']=stats.getvalue();out['elapsed_s']=time.monotonic()-started
print(json.dumps(out))
