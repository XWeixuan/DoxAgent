"""Bounded production receipts, no model calls or business writes."""
import json
from pathlib import Path
import subprocess
from datetime import datetime,timezone

root=Path('/var/lib/doxagent/releases/w3-main-20261116')
code=r'''
import sqlite3,json
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
p=Path(DoxAgentSettings().persistent_runtime_v2_sqlite_path)
db=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1);db.row_factory=sqlite3.Row
codec=NativeContent(p)
out={}
for ns,key in [('execution','active'),('w3_entry','active'),('w3_entry','pending'),('w3_trade_config','mode')]:
 row=db.execute('select payload from runtime_values where namespace=? and key=?',(ns,key)).fetchone()
 out[ns+':'+key]=json.loads(row[0]) if row else None
out['w3_entry_by_ticker']=[dict(r) for r in db.execute("select key,payload from runtime_values where namespace='w3_entry_active'")]
identity=out.get('execution:active')
if identity:
 row=db.execute('select payload from runtime_snapshots where id=?',(identity,)).fetchone()
 value=codec.decode(json.loads(row[0]))
 out['active_contracts']={k:v for k,v in value.get('contracts',{}).items() if k in ('w3_contract_version','w3_entry_version','w3_asset_layout')}
out['effects']=[dict(r) for r in db.execute("select effect_type,status,count(*) as n from runtime_v2_effects where status in ('RUNNING','PENDING','PENDING_RETRY') group by effect_type,status")]
out['recent_stages']=[dict(r) for r in db.execute("select case_id,phase,status,thread_id,worker_job_id,accepted_at,committed_at,json_extract(result_json,'$.decision') as direction,json_extract(input_refs,'$.result_source') as source,json_extract(input_refs,'$.input_sha') as input_sha,json_extract(input_refs,'$.request.model') as model,json_extract(input_refs,'$.request.effort') as effort from runtime_v2_w3_stages order by accepted_at desc limit 12")]
out['latest_cases']=[dict(r) for r in db.execute("select ticker,case_id,status,created_at,w3_contract_version from (select ticker,case_id,status,created_at,json_extract(payload_json,'$.w3_contract_version') as w3_contract_version from runtime_v2_cases order by created_at desc limit 5)")]
print(json.dumps(out))
'''
result=json.loads(subprocess.check_output(['docker','exec','-i','doxagent-v2-v2-scheduler-1','python','-'],input=code.encode()))
result['at']=datetime.now(timezone.utc).isoformat()
result['memory']={}
for line in Path('/proc/meminfo').read_text().splitlines():
 key,_,value=line.partition(':')
 if key in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):result['memory'][key]=value.strip()
result['vmstat']={k:int(v) for k,v in (l.split() for l in Path('/proc/vmstat').read_text().splitlines()) if k in ('pswpin','pswpout','pgmajfault')}
result['containers']=subprocess.check_output(['docker','ps','--format','{{.Names}} {{.Status}}']).decode().splitlines()
with (root/'observation.jsonl').open('a') as f:f.write(json.dumps(result)+'\n')
print(json.dumps(result))
