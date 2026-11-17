import sqlite3,json,os,signal,collections
from pathlib import Path
os.nice(15);signal.alarm(35);R=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def db(p):
 c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1);c.row_factory=sqlite3.Row;return c
d=db(R/'runtime/runtime.sqlite3');s=db(R/'codex-home/state_5.sqlite')
rows=[dict(r) for r in d.execute("select case_id,phase,thread_id,status,accepted_at,committed_at,worker_job_id from runtime_v2_w3_stages order by rowid desc limit 70000")];tr=[r for r in rows if r['phase']=='trade_decision'];win=[r for r in tr if '2026-11-12T05:00'<=r['accepted_at']<'2026-11-14T05:00'];ids={r['thread_id'] for r in win if r['thread_id']};threads=[]
for tid in ids:
 hist=sorted([r for r in tr if r['thread_id']==tid],key=lambda r:r['accepted_at']);reg=d.execute('select record_id,ticker,kind,parent_id,last_received_at,expires_at,status from runtime_v2_w3_threads where thread_id=?',(tid,)).fetchone();native=s.execute('select rollout_path from threads where id=?',(tid,)).fetchone()
 links=[dict(r) for r in d.execute('select link_kind,subject_key,source_case_id from runtime_v2_w3_thread_links where record_id=?',(reg['record_id'],))] if reg else []
 threads.append(dict(thread_id=tid,registry=dict(reg) if reg else None,history=hist,links=links,rollout_path=native[0] if native else None))
print(json.dumps(dict(rows=len(rows),oldest=min(r['accepted_at'] for r in rows),cases=win,threads=threads)))
