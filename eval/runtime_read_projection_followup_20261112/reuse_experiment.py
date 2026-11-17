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


from doxagent.v2_read.libraries import LibraryIndexer
from doxagent.v2_read.artifacts import PublishedArtifacts
from doxagent.v2_read.repository import encode
store=ReadOnlyStore('/data/read/serving-v4-20261018/v2.sqlite3');source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime');mapper=FormalProjectors(store,PublishedArtifacts('/data/research/research.sqlite3'),'/data/events')
coordinate=4527789
out={'at':datetime.now(timezone.utc).isoformat(),'coordinate':coordinate}
with store.projection_session():
 e=source.event(coordinate,serving=True);e['source']='runtime';t=time.perf_counter();baseline,counts=mapper(e);out['baseline_seconds']=time.perf_counter()-t
 import doxagent.v2_read.reference as module
 reused=[]
 def registered(root,ticker,captured):
  path=Path(root)/'US'/ticker/'event_library.sqlite3';version=captured['version']
  if not version:return None,[],{},{}
  with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as d:
   row=d.execute("SELECT identity,provenance FROM v2_native_identities WHERE collection='library_versions' AND native_key=?",(encode([ticker,version]),)).fetchone()
   ref=store.get('library',ticker,row[0]) if row and row[1]=='RECORDED' else None
   if not ref:raise ValueError('EXPERIMENT_REQUIRES_REGISTERED_LIBRARY')
   events={ev['event_id'] for ev in captured['events']};facts={f['fact_id'] for ev in captured['events'] for f in ev['facts']}
   keys={encode([ticker,int(n[1:])]) for n in events|facts}
   ids={(c,key):identity for c,key,identity,proof in d.execute("SELECT collection,native_key,identity,provenance FROM v2_native_identities WHERE collection IN ('canonical_events','canonical_facts') AND native_key IN (SELECT value FROM json_each(?))",(json.dumps(list(keys)),)) if proof=='RECORDED'}
  by_id={n:{'event_key':ids[('canonical_events',encode([ticker,int(n[1:])]))]} for n in events}
  fact_keys={n:ids[('canonical_facts',encode([ticker,int(n[1:])]))] for n in facts}
  reused.append((ticker,version,ref['library_snapshot_id']));return ref,[],by_id,fact_keys
 module.registered=registered
 text=Path('/app/src/doxagent/v2_read/reference.py').read_text()
 a=text.index('        ref, indexed = (');z=text.index('        events[side] = {}',a)
 text=text[:a]+"        ref,indexed,by_id,facts = registered(value.get(side + '_root',value['root']),ticker,captured)\n        refs[side] = ref\n        records.extend(indexed)\n"+text[z:]
 exec(compile(text,'diagnostic-reference-only','exec'),module.__dict__)
 t=time.perf_counter();candidate,counts2=mapper(e);out['candidate_seconds']=time.perf_counter()-t
 # Every canonical record omitted by the experiment must already be identical.
 by_id={(r['kind'],r['ticker'],r['id']):r for r in candidate};differences=[];already_equal=0
 for r in baseline:
  key=(r['kind'],r['ticker'],r['id'])
  if key in by_id:
   if by_id[key]!=r:differences.append({'kind':r['kind'],'id':r['id'],'reason':'derived_changed'})
  else:
   current=store.get(*key)
   if current!=r.get('data'):differences.append({'kind':r['kind'],'id':r['id'],'reason':'not_already_equal'})
   else:already_equal+=1
 out.update(baseline_records=len(baseline),candidate_records=len(candidate),already_identical_omissions=already_equal,contributions_equal=counts==counts2,differences=differences[:15],difference_count=len(differences),reused=reused)
print(json.dumps(out))
