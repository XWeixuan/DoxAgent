"""Read-only production acceptance; no model turn or broker call."""
import asyncio,hashlib,json,os,sqlite3,tomllib,urllib.request
from pathlib import Path
from types import SimpleNamespace
from doxagent.persistent_runtime_v2.w3_models import freeze_case_profile
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.turn_usage import rollout_path

out={'release':os.environ.get('DOXAGENT_BUILD_COMMIT'),'profiles':{},'hashes':{},'native_ready_settings':[]}
for label,hit,related in [('HIT',True,False),('RELATED',False,True),('OPEN',False,False)]:
 case=SimpleNamespace(w2_final=SimpleNamespace(policy_hit=hit,policy_related=related),frozen_inputs={})
 out['profiles'][label]=freeze_case_profile(case)
for name in ['persistent_runtime_v2/w3_models.py','persistent_runtime_v2/w3_execution.py','codex_worker/app.py','codex_worker/w3_ready.py','codex_worker/sdk_runtime.py']:
 p=Path('/app/src/doxagent')/name;out['hashes'][name]=hashlib.sha256(p.read_bytes()).hexdigest()
settings=DoxAgentSettings()
req=urllib.request.Request('http://127.0.0.1:8791/v1/w3/readiness',headers={'Authorization':'Bearer '+settings.codex_worker_bearer_token})
out['readiness']=json.loads(urllib.request.urlopen(req,timeout=10).read())
home=Path(os.environ['CODEX_HOME'])
cfg=tomllib.loads((home/'config.toml').read_text()) if (home/'config.toml').exists() else {}
out['global_service_tier']=cfg.get('service_tier')
db=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True)
for config,thread in db.execute("SELECT config,thread_id FROM w3_ready_slots WHERE state='READY'"):
 if not thread:continue
 path=rollout_path(home,thread)
 if path and path.exists():
  native=[json.loads(l) for l in path.read_text().splitlines()]
  found=[r['payload']['thread_settings'] for r in native if r.get('payload',{}).get('type')=='thread_settings_applied']
  if found:out['native_ready_settings'].append({'model':json.loads(config)['profile']['model'],'thread_id':thread,'service_tier':found[-1].get('service_tier'),'native_model':found[-1].get('model')})
async def sdk_config_smoke():
 from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
 from doxagent.codex_worker.schema import WorkerRunRequest
 import tempfile
 captured={}
 class Client:
  async def thread_resume(self,*a,**kw):captured.update(kw);return SimpleNamespace(id='smoke-no-sdk-call')
 runtime=OpenAICodexRuntime(settings=settings,preserve_requested_model=True,container_isolated=True)
 runtime._client=Client()
 with tempfile.TemporaryDirectory(prefix='w3-policy-config-') as path:
  request=WorkerRunRequest(workflow_version='persistent_runtime_w3_v1',research_lane='persistent_runtime',run_id='config-smoke',ticker='MU',node='persistent_runtime_w3',agent_role='w3_duty_trading_expert',attempt_id='config-only',prompt='not executed',output_schema={'type':'object'},thread_id='smoke-no-sdk-call',model='gpt-6.1-sol',effort='medium',data_mcp_enabled=False,research_assets_mcp_enabled=True,w3_entry_version='w3-entry-v5',task_phase='trade_decision')
  await runtime.prepare_resume(request,Path(path))
 return {'service_tier':captured['config'].get('service_tier'),'model':captured.get('model'),'model_calls':0}
out['sdk_config_smoke']=asyncio.run(sdk_config_smoke())
# Observe naturally admitted jobs only; historical frozen jobs may keep Luna.
out['recent_jobs']=[]
for payload,request in db.execute("SELECT payload,request FROM jobs ORDER BY sequence DESC LIMIT 25"):
 j=json.loads(payload);r=json.loads(request) if request else {}
 if r.get('task_phase') in ['trade_decision','w3_template']:
  out['recent_jobs'].append({k:r.get(k) for k in ['task_phase','model','effort','ticker']}|{k:j.get(k) for k in ['job_id','created_at','status','thread_id']})
from doxagent.v2_read.native_content import NativeContent
runtime=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True)
codec=NativeContent('/data/runtime/runtime.sqlite3')
out['new_frozen_cases']=[]
for payload, in runtime.execute('SELECT payload_json FROM runtime_v2_cases ORDER BY rowid DESC LIMIT 60'):
 case=json.loads(payload)
 if case.get('created_at','')<'2026-10-20T13:22:00':continue
 frozen=codec.decode(case.get('frozen_inputs',{}))
 profile=frozen.get('w3_execution_profile')
 if not profile:continue
 w2=codec.decode(case.get('w2_final',{})) or {}
 item={'case_id':case['case_id'],'profile':profile,'policy_hit':w2.get('policy_hit'),'policy_related':w2.get('policy_related'),'status':case.get('status')}
 stage=runtime.execute("SELECT worker_job_id,status FROM runtime_v2_w3_stages WHERE case_id=? AND phase='trade_decision'",(case['case_id'],)).fetchone()
 if stage:
  item['stage']={'worker_job_id':stage[0],'status':stage[1]}
  raw=db.execute('SELECT payload FROM jobs WHERE id=?',(stage[0],)).fetchone()
  if raw:
   j=json.loads(raw[0]);item['job']={k:j.get(k) for k in ['job_id','status','thread_id','turn_id','error_code']}
   path=rollout_path(home,j['thread_id']) if j.get('thread_id') else None
   if path and path.exists():
    native=[json.loads(l) for l in path.read_text().splitlines()]
    contexts=[r['payload'] for r in native if r['type']=='turn_context' and r['payload'].get('turn_id')==j.get('turn_id')]
    item['native_contexts']=[{k:c.get(k) for k in ['turn_id','model','effort']} for c in contexts]
 out['new_frozen_cases'].append(item)
print(json.dumps(out))
