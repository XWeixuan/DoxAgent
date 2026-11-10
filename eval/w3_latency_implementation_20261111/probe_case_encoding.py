"""Read one completed Case; all encoding writes target a disposable /tmp directory."""
import json
import os
import signal
import sqlite3
import tempfile
import time
from pathlib import Path

os.nice(19)
signal.alarm(30)
from doxagent.persistent_runtime_v2.schema import RuntimeCase
from doxagent.v2_read.native_content import NativeContent

path='/data/runtime/runtime.sqlite3'
cid='case_994e1c492da7438d956cb99358f0c550'
db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=.1)
try:
    raw=db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(cid,)).fetchone()[0]
finally:
    db.close()
case=RuntimeCase.model_validate(NativeContent(path).decode(json.loads(raw)))
rows=[]
with tempfile.TemporaryDirectory(prefix='w3-encode-probe-') as directory:
    codec=NativeContent(Path(directory)/'probe.db')
    for label,include in [('old_full_case',None),('phase_results',{'w3_trade_decision','w3_operations_result'})]:
        a=time.monotonic();cpu=time.process_time()
        encoded=codec.encode(case.model_dump(mode='json',include=include))
        rows.append(dict(label=label,wall_ms=(time.monotonic()-a)*1000,cpu_ms=(time.process_time()-cpu)*1000,
                         payload_bytes=len(encoded.encode())))
print(json.dumps(dict(case_id=cid,production_writes=0,model_calls=0,rows=rows)))
