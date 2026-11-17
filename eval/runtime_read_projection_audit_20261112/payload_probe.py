import json
import time
from datetime import datetime, timezone
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.native_content import MARKER
from doxagent.v2_read.content_files import ContentFiles

s=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
with s.connection() as db:
    head=s.head()
    rows=[dict(r) for r in db.execute("SELECT o.seq,r.payload FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq>? AND o.table_name='runtime_v2_cases' ORDER BY o.seq LIMIT 24",(head-1200,))]
original=ContentFiles.read_all
reads=[]
def track(self,identity):
    t=time.monotonic(); raw=original(self,identity)
    reads.append({'bytes':len(raw),'ms':(time.monotonic()-t)*1000,'digest':identity})
    return raw
ContentFiles.read_all=track
def markers(x,path=''):
    found=[]
    if isinstance(x,dict):
        if MARKER in x:found.append({'path':path,'bytes':x.get('bytes')})
        else:
            for k,v in x.items():found.extend(markers(v,path+'.'+k))
    elif isinstance(x,list):
        for i,v in enumerate(x):found.extend(markers(v,path+'[]'))
    return found
out={'at':datetime.now(timezone.utc).isoformat(),'samples':[]}
for r in rows:
    outer=json.loads(r['payload']);raw=outer.get('payload_json')
    value=json.loads(raw) if isinstance(raw,str) else raw
    before=len(reads);t=time.monotonic();decoded=s.content.source_row(outer);elapsed=(time.monotonic()-t)*1000
    decoded=json.loads(decoded['payload_json'])
    out['samples'].append({'seq':r['seq'],'envelope_bytes':len(r['payload']),'native_markers':markers(value),
                           'decode_ms':elapsed,'file_reads':reads[before:],'decoded_field_bytes':{k:len(json.dumps(v)) for k,v in decoded.items() if len(json.dumps(v))>5000},
                           'frozen_field_bytes':{k:len(json.dumps(v)) for k,v in decoded.get('frozen_inputs',{}).items()}})
print(json.dumps(out))
