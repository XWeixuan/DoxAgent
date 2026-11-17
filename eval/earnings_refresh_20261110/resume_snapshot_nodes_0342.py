import json,hashlib,sqlite3
from pathlib import Path
from doxagent.ticker_initialization.repository import InitializationRepository
for t,d in [('ceg','20261110'),('rklb','20261111')]:
 root=Path('/data/ops/'+t+'-refresh-'+d);m=json.loads((root/'submitted.json').read_text());rid=m['run']['initialization_id'];r=InitializationRepository(m['control_database']);assert r.get(rid).status=='FAILED'
 with sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True) as c:assert not c.execute("select 1 from jobs where status in ('queued','running') and request like ?",('%'+rid+'%',)).fetchone()
 with r._connection() as c:
  assert not list(c.execute('select * from ticker_operations'));assert not c.execute("select 1 from initialization_repair_incidents where status in ('ACTIVE','HUMAN_REQUIRED')").fetchone()
  rows=[(k,json.loads(p)) for k,p in c.execute('select node_key,payload from initialization_nodes where run_id=?',(rid,))]
  preserved={k:hashlib.sha256(p.encode()).hexdigest() for k,p in c.execute("select node_key,payload from initialization_nodes where run_id=? and json_extract(payload,'$.status')='SUCCEEDED'",(rid,))}
 targets=[k for k,n in rows if n['status']=='FAILED' and k.startswith('d3.') and '/snapshots/' in (n.get('error') or '')];assert len(targets)==(5 if t=='ceg' else 1),targets
 proof=root/'snapshot-recovery-20261112-0342.json';assert not proof.exists();proof.write_text(json.dumps({'before':r.get(rid).model_dump(mode='json'),'targets':targets,'preserved':preserved,'failed_snapshot_nodes':{k:n for k,n in rows if k in targets}},ensure_ascii=False))
 # Use only the formal selected-node resume API. Between selections, close
 # the operator-owned lease as failed staging; no research work is dispatched.
 # This avoids selecting an unrelated old D1 transport failure.
 for i,key in enumerate(targets):
  r.resume(rid,node_key=key,reason='Verified EMLINK snapshot infrastructure repair: identical original snapshot RPC succeeded; retry only this unexecuted snapshot child, preserve all successful nodes and original cutoff.')
  if i<len(targets)-1:
   lease=r.claim('snapshot-recovery-staging-0342',lease_seconds=600);assert lease and lease.initialization_id==rid
   r.finish(lease,error='Operator staging selected failed snapshot children only; no model dispatch; continue next audited selection')
  print(t,'selected_resume',key,flush=True)
 with r._connection() as c:
  after={k:hashlib.sha256(p.encode()).hexdigest() for k,p in c.execute("select node_key,payload from initialization_nodes where run_id=? and json_extract(payload,'$.status')='SUCCEEDED'",(rid,))}
 assert all(after.get(k)==h for k,h in preserved.items());print(t,'QUEUED','preserved',len(preserved),flush=True)
