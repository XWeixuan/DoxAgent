"""Selected frozen payload and saved-result read/parse probe. No production writes."""
import json
import os
import signal
import sqlite3
import time
from contextlib import closing

os.nice(19);signal.alarm(30)
from doxagent.v2_read.native_content import NativeContent
from doxagent.persistent_runtime_v2.schema import RuntimeCase
from doxagent.persistent_runtime_v2.w3_contracts import contract_models

path='/data/runtime/runtime.sqlite3';codec=NativeContent(path)
rows=[]
for cid in ['case_f438f9c5998f4e3a98a5bbb46fa6dddd','case_dc48755e0334446eb95733ac52dfdb03','case_1221cbd4d4074a0e96fa091d5d94be37']:
    a=time.monotonic()
    with closing(sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=.1)) as db:
        db.execute('PRAGMA query_only=ON')
        raw=db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(cid,)).fetchone()[0]
        stages=db.execute('SELECT phase,result_json FROM runtime_v2_w3_stages WHERE case_id=?',(cid,)).fetchall()
    b=time.monotonic();c=time.process_time()
    text=codec.text(raw)
    d=time.monotonic();e=time.process_time()
    value=json.loads(text);case=RuntimeCase.model_validate(value)
    f=time.monotonic();g=time.process_time()
    parsed=[];models=contract_models(case.w3_contract_version)
    for phase,result in stages:
        start=time.monotonic();model=models[0 if phase=='trade_decision' else 1]
        validated=model.model_validate(json.loads(result))
        parsed.append(dict(phase=phase,bytes=len(result),parse_validate_ms=(time.monotonic()-start)*1000,
                           delta_candidates=len(getattr(validated,'delta_candidates',[]))))
    rows.append(dict(case_id=cid,sql_read_ms=(b-a)*1000,expanded_bytes=len(text.encode()),
        native_expand_reencode_ms=(d-b)*1000,native_expand_reencode_cpu_ms=(e-c)*1000,
        case_json_pydantic_ms=(f-d)*1000,case_json_pydantic_cpu_ms=(g-e)*1000,stages=parsed))
print(json.dumps({'rows':rows,'sdk_constructed':False,'model_calls':0}))
