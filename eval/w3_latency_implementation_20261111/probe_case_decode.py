"""Read-only native Case decode comparison; no cache or production writes."""
import json
import os
import signal
import sqlite3
import time
os.nice(19);signal.alarm(30)
from doxagent.persistent_runtime_v2.schema import RuntimeCase
from doxagent.v2_read.native_content import NativeContent
path='/data/runtime/runtime.sqlite3';codec=NativeContent(path)
ids=['case_994e1c492da7438d956cb99358f0c550','case_5ec9df499bb64ecba48496e14f6386d5']
rows=[]
with sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=.1) as db:
    for cid in ids:
        raw=db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(cid,)).fetchone()[0]
        values=[]
        for label in ['roundtrip','direct']:
            a=time.monotonic();c=time.process_time()
            value=(RuntimeCase.model_validate_json(codec.text(raw)) if label=='roundtrip'
                   else RuntimeCase.model_validate(codec.decode(json.loads(raw))))
            rows.append(dict(case_id=cid,label=label,wall_ms=(time.monotonic()-a)*1000,cpu_ms=(time.process_time()-c)*1000))
            values.append(value)
        assert values[0]==values[1]
print(json.dumps(dict(rows=rows,equal=True,production_writes=0,model_calls=0)))
