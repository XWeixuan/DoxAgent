"""Read-only production census; print a compact, reproducible audit snapshot."""
import json, sqlite3
from datetime import datetime, UTC
from doxagent.v2_read.native_content import NativeContent

path='/data/runtime/runtime.sqlite3'
db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=10)
db.row_factory=sqlite3.Row
db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
codec=NativeContent(path)
def load(raw): return codec.decode(json.loads(raw))
start='2026-10-13T16:00:00+00:00'
end=datetime.now(UTC).isoformat()
rows=[]; all_counts={}; outside=0
for r in db.execute("SELECT key,payload FROM runtime_values WHERE namespace='trade_intents'"):
    v=load(r['payload']); t=v.get('released_at') or v.get('created_at')
    all_counts[v.get('ticker','UNKNOWN')]=all_counts.get(v.get('ticker','UNKNOWN'),0)+1
    if not t or not start<=t<=end: outside+=1; continue
    cid=v.get('case_id') or r['key'].removeprefix('trade:')
    cr=db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(cid,)).fetchone()
    case=load(cr[0]) if cr else None
    w3s=[load(w[0]) for w in db.execute('SELECT payload_json FROM runtime_v2_w3_cases WHERE case_id=?',(cid,))]
    def compact(x):
        if isinstance(x,dict):
            return {k:compact(val) for k,val in x.items() if k not in ('body','content','frozen_inputs','w3_context','context','messages','prompt','input_payload')}
        if isinstance(x,list): return [compact(z) for z in x]
        return x
    er=db.execute('SELECT payload FROM te_executions WHERE id=?',(v['intent_id'],)).fetchone()
    rows.append(dict(key=r['key'],intent=compact(v),case=compact(case),w3=compact(w3s),execution=compact(load(er[0])) if er else None))
print(json.dumps(dict(start=start,captured_at=end,total_durable_intents=sum(all_counts.values()),all_counts=all_counts,outside_window=outside,intents=rows),ensure_ascii=False))
