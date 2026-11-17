"""Isolated frozen-input probes. No business writes or configuration changes."""
import importlib.util,json,sqlite3,time,uuid,sys,copy,re
from pathlib import Path
from dataclasses import replace
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
from doxagent.v2_read.usage import accounting
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesRequest,RuntimeResponsesError
from doxagent.persistent_runtime_v2.schema import W1Round1Result,W1FactExtractionResult,W2PolicyAssessmentResultV2
from doxagent.persistent_runtime_v2.openrouter import _cache_optimized_input
from doxagent.persistent_runtime_v2 import openai_platform as baseline

spec=importlib.util.spec_from_file_location('doxagent.persistent_runtime_v2.cache_candidate','/tmp/cache-cost-openai-platform-20261117.py')
candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)
s=DoxAgentSettings();p=s.persistent_runtime_v2_sqlite_path
db=sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True,timeout=2);db.execute('pragma query_only=on')
codec=NativeContent(p)
saved=Path('/tmp/cache-cost-live-20261117.json')
state=json.loads(saved.read_text()) if saved.exists() else {}
run=state.get('run') or uuid.uuid4().hex[:12];receipts=state.get('receipts',[])
def frozen(cid,rnd,lane='W1'):
 row=db.execute("select payload from runtime_values where namespace='round_inputs' and key=?",(cid+':'+lane+':'+rnd,)).fetchone()
 if not row:raise ValueError('missing frozen round '+cid+':'+rnd)
 return codec.decode(json.loads(row[0]))
def request(cid,rnd,lane='W1'):
 f=frozen(cid,rnd,lane);m=f['model_settings'];parent=None
 if rnd=='R3':
  pf=frozen(cid,'R2');text,_=_cache_optimized_input(pf['payload'],tuple(pf['cache_context_keys']))
  row=db.execute("select payload_json from runtime_v2_turns where case_id=? and lane='W1' and round_name='R2' and status='OK' order by attempt_number desc limit 1",(cid,)).fetchone()
  t=codec.decode(json.loads(row[0]));parent=(text,t['raw_output'] or json.dumps(t['output'],ensure_ascii=False))
 payload=copy.deepcopy(f['payload'])
 if lane=='W2':
  for policy in payload['policy_details']['policies']:
   policy.pop('transmission',None)
   for condition in policy['activation_conditions']:condition.pop('trigger_layer',None)
 return RuntimeResponsesRequest(instructions=f['instructions'],payload=payload,
  output_model=W2PolicyAssessmentResultV2 if lane=='W2' else W1Round1Result if rnd=='R1' else W1FactExtractionResult,
  schema_name='w2_policy_result' if lane=='W2' else 'w1_round1_result' if rnd=='R1' else 'w1_fact_extraction_result',
  model='gpt-6-luna',provider='openai',provider_endpoint=baseline.ENDPOINT,transport='responses_json_schema',
  reasoning_effort='medium' if lane=='W1' and rnd=='R3' else 'high',cache_ttl='12h' if rnd=='R1' else '30m',
  timeout_seconds=180 if rnd=='R1' else 300,cache_context_keys=tuple(f['cache_context_keys']),
  provisional_cache_batch_size=m.get('provisional_cache_batch_size',5),replay_parent=parent,
  metadata={'lane':lane,'round':rnd,'service_tier':'default' if lane=='W1' and rnd=='R3' else 'fast',
   'case_cache_layout':m.get('case_cache_layout',''),'w1_daily_index':'w1-daily-known-index.v1'})
def call(module,arm,cid,rnd,lane='W1'):
 for old in receipts:
  if old.get('lane','W1')==lane and old['round']==rnd and old['arm']==arm and old['case_id']==cid and old['status']=='OK':return old
 r=request(cid,rnd,lane);build=module.build_request
 def isolated(req):
  body,fp=build(req);body['prompt_cache_key']='cache-eval-'+run+'-'+arm+'-'+lane+'-'+rnd
  return body,fp
 module.build_request=isolated
 client=module.OpenAIPlatformClient(s.openai_api_key.get_secret_value() if s.openai_api_key else None)
 try:
  started=time.perf_counter();v=client.complete(r)
  receipt={k:getattr(v,k) for k in ['response_id','latency_ms','input_tokens','output_tokens','reasoning_tokens','cached_input_tokens','cache_write_input_tokens','provider_usage']}
  n,m=accounting(r.model,'openai','API',None,{**receipt,'cache_ttl':r.cache_ttl,'provider_endpoint':r.provider_endpoint})
  receipt.update(case_id=cid,arm=arm,lane=lane,round=rnd,status='OK',wall_seconds=time.perf_counter()-started,
   normalized=n,money={k:str(v) if v is not None else None for k,v in m.items()},output=v.value.model_dump(mode='json'))
  if lane=='W2':
   policies=r.payload['policy_details']['policies'];byid={p['policy_id']:{c['condition_id'] for c in p['activation_conditions']} for p in policies}
   assert (set(v.value.policy_ids)|set(v.value.related_policy_ids))<=set(byid)
   assert all(set(g.condition_ids)<=byid[g.policy_id] for g in v.value.hit_condition_ids)
   old=json.dumps(frozen(cid,rnd,lane)['payload']['policy_details'],ensure_ascii=False)
   new=json.dumps(r.payload['policy_details'],ensure_ascii=False)
   receipt['policy_chars_before']=len(old);receipt['policy_chars_after']=len(new)
 except RuntimeResponsesError as e:
  receipt={'case_id':cid,'arm':arm,'round':rnd,'status':'ERROR','error_code':e.code,'message':str(e)[:400],
   'cause_type':type(e.__cause__).__name__,'cause':str(e.__cause__)[:300],'usage':e.usage}
 finally:client.close();module.build_request=build
 receipts.append(receipt)
 Path('/tmp/cache-cost-live-20261117.json').write_text(json.dumps({'run':run,'receipts':receipts},ensure_ascii=False,indent=2))
 print(json.dumps({k:v for k,v in receipt.items() if k not in ['output','usage','money','normalized','provider_usage']},ensure_ascii=False),flush=True)
 return receipt

cases=['case_6becc9e627614102898e0897df26ba70','case_fa8ccaaa3ef34aa3b6a1b1ef84c29182','case_579181460c874e6a91d9bd20f79e74f8']
for arm,module in [('old',baseline),('new',candidate)]:
 for cid in cases:
  if call(module,arm,cid,'R1')['status']!='OK':sys.exit(1)
# Real business schema, frozen R2 parent and new default/medium parameters.
cid=db.execute("select c.case_id from runtime_v2_cases c join runtime_v2_turns t on t.case_id=c.case_id where c.trading_date='2026-11-16' and t.lane='W1' and t.round_name='R3' and t.status='OK' order by t.created_at desc limit 1").fetchone()[0]
call(candidate,'new',cid,'R3')
other=db.execute("select c.case_id from runtime_v2_cases c join runtime_v2_turns t on t.case_id=c.case_id where c.trading_date='2026-11-16' and c.case_id!=? and t.lane='W1' and t.round_name='R3' and t.status='OK' order by t.created_at desc limit 1",(cid,)).fetchone()[0]
call(candidate,'new',other,'R3')
third=db.execute("select c.case_id from runtime_v2_cases c join runtime_v2_turns t on t.case_id=c.case_id where c.trading_date='2026-11-16' and c.case_id not in (?,?) and t.lane='W1' and t.round_name='R3' and t.status='OK' order by t.created_at desc limit 1",(cid,other)).fetchone()[0]
call(candidate,'new',third,'R3')
for flag in [1,0]:
 row=db.execute("select c.case_id from runtime_v2_cases c join runtime_v2_turns t on t.case_id=c.case_id where c.trading_date='2026-11-16' and t.lane='W2' and t.round_name='R2' and t.status='OK' and json_extract(t.payload_json,'$.output.policy_hit')=? order by t.created_at desc limit 1",(flag,)).fetchone()
 if row:call(candidate,'new',row[0],'R2','W2')
