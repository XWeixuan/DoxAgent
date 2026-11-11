import subprocess
CODE='''import os,json,sqlite3,tempfile
from pathlib import Path
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.decision_metrics import project
def ro(path):return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
rt=ro(os.environ['DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH']);jobs=ro(Path(os.environ['DOXAGENT_CODEX_WORKSPACE_ROOT'])/'worker-jobs.sqlite3')
cid='case_4a503841efae46f18d271349b1fcfd8a';ticker='RKLB'
created,mode,decision=rt.execute("SELECT created_at,json_extract(payload_json,'$.runtime_mode'),json_extract(payload_json,'$.w3_trade_decision') FROM runtime_v2_cases WHERE case_id=?",(cid,)).fetchone()
records=[dict(kind='native:runtime_v2_cases',id=cid,data=dict(created_at=created,runtime_mode=mode,w3_trade_decision=json.loads(decision)))]
for turn,lane,interval in rt.execute("SELECT turn_id,lane,json_extract(payload_json,'$.model_interval') FROM runtime_v2_turns WHERE case_id=?",(cid,)):
 records.append(dict(kind='attempt',id=turn,parent=cid,data=dict(node_id=lane,status='SUCCEEDED',model_interval=json.loads(interval) if interval else None)))
jid=rt.execute("SELECT worker_job_id FROM runtime_v2_w3_stages WHERE case_id=? AND phase='trade_decision'",(cid,)).fetchone()[0]
trade_jid=jid
run=jobs.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE id=?",(jid,)).fetchone()[0]
for jid,status,phase,timing in jobs.execute("SELECT id,status,json_extract(request,'$.task_phase'),json_extract(payload,'$.resource_receipt.execution_timing') FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.attempt_id') LIKE ?",(run,cid[-24:]+'-%')):
 t=json.loads(timing) if timing else {}
 phase='trade_decision' if jid==trade_jid else 'post_decision_operations'
 records.append(dict(kind='attempt',id=jid,parent=cid,data=dict(status=status.upper(),phase=phase,model_interval={'start':t.get('sdk_dispatched_at'),'end':t.get('sdk_completed_at')},model_completed_at=t.get('model_completed_at'))))
for eid,effective in rt.execute("SELECT effect_id,json_extract(payload_json,'$.payload.decision_effective_at') FROM runtime_v2_effects WHERE case_id=?",(cid,)):
 if effective:records.append(dict(kind='native:runtime_v2_effects',id=eid,parent=cid,data={'payload':{'decision_effective_at':effective}}))
with tempfile.TemporaryDirectory(prefix='metrics-shadow-') as folder:
 store=ReadStore(Path(folder)/'read.db');store.migrate()
 result,tag,counts=project(store,ticker,dict(case_id=cid,semantic_day='2026-11-11',received_at=created),records)
 print(json.dumps({'source_case':cid,'production_writes':0,'model_calls':0,'shadow_result':result,'counts':{c['metric']:c['value'] for c in counts}}))
'''
cid=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.service=v2-scheduler'],text=True).strip()
p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=CODE,text=True,capture_output=True,timeout=20)
print(p.stdout)
if p.returncode:raise RuntimeError(p.stderr[-1000:])
