"""Selected source keys only; no frozen prompt, tool content or SDK call."""
import json
import sqlite3
from pathlib import Path

root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out=[]
for cid in ('case_b6aecbececbf4bf4a347743b4784f2ce','case_fbef85346c3e4a869bcf876fe7e00333'):
    with sqlite3.connect((root/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
        rows=d.execute("SELECT phase,accepted_at,committed_at,length(input_refs),json_extract(input_refs,'$.workspace') "
                       "FROM runtime_v2_w3_stages WHERE case_id=?",(cid,)).fetchall()
    for phase,accepted,committed,size,run in rows:
        if not run:continue
        slot=run.removeprefix('w3-ready-')
        with sqlite3.connect((root/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
            binding=d.execute('SELECT created_at,bound_at,ready_at,execution_lane FROM w3_ready_slots WHERE id=?',(slot,)).fetchone()
            op=d.execute('SELECT created_at,updated_at,fork_dispatched_at,startup_receipt,step FROM w3_thread_operations WHERE operation_key=?',('w3-slot-fork:'+slot,)).fetchone()
        out.append(dict(case_id=cid,phase=phase,accepted=accepted,committed=committed,refs_bytes=size,
                        slot=slot,binding=binding,operation=op))
print(json.dumps(out))
