"""Selected completed Case read-only projection-work probe; no SDK or writes."""
from contextlib import closing
import json
import os
import signal
import sqlite3
import time

os.nice(19);signal.alarm(20)
from doxagent.v2_read.native_content import NativeContent
from doxagent.persistent_runtime_v2.schema import RuntimeModelTurn

path='/data/runtime/runtime.sqlite3';codec=NativeContent(path);rows=[]
for cid in ['case_f350e9422c084dd29616e297d494be84','case_70cc9da04d95481a9cdfdeebc59b6f0c']:
    a=time.monotonic()
    with closing(sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=.1)) as db:
        raw=db.execute('SELECT payload_json FROM runtime_v2_turns WHERE case_id=?',(cid,)).fetchall()
    b=time.monotonic();size=0
    for row in raw:
        text=codec.text(row[0]);size+=len(text.encode());RuntimeModelTurn.model_validate_json(text)
    c=time.monotonic()
    with closing(sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=.1)) as db:
        count=db.execute('SELECT count(*) FROM runtime_v2_turns WHERE case_id=?',(cid,)).fetchone()[0]
    d=time.monotonic()
    rows.append(dict(case_id=cid,count=count,expanded_bytes=size,full_read_ms=(b-a)*1000,
                     decode_validate_ms=(c-b)*1000,indexed_count_ms=(d-c)*1000))
print(json.dumps(dict(rows=rows,model_calls=0,writes=0)))
