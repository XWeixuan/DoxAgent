import json,sqlite3,hashlib,inspect,time
from pathlib import Path
from datetime import datetime,UTC
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
from doxagent.persistent_runtime_v2 import openai_platform,service,future_model_profiles
from doxagent.semantic_clock import semantic_day
s=DoxAgentSettings();p=Path(s.persistent_runtime_v2_sqlite_path).resolve();codec=NativeContent(p)
db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row;db.execute('pragma query_only=on')
deadline=time.monotonic()+30;db.set_progress_handler(lambda:int(time.monotonic()>deadline),50000)
def value(ns,key):
 row=db.execute('select payload from runtime_values where namespace=? and key=?',(ns,key)).fetchone()
 return codec.decode(json.loads(row[0])) if row else None
def bundle(key):return codec.decode(json.loads(db.execute('select payload from runtime_snapshots where id=?',(key,)).fetchone()[0]))
before=json.loads(Path('/tmp/runtime-cache-activation-before.json').read_text())
old=bundle(before['active']);identity=value('execution','active');new=bundle(identity)
a=json.loads(old['assets']['runtime_models.json']);b=json.loads(new['assets']['runtime_models.json'])
changed=[k for k in b['rounds'] if a['rounds'].get(k)!=b['rounds'][k]]
assert changed==['W1:R3'],changed
assert b['rounds']['W1:R3']['reasoning_effort']=='medium' and b['rounds']['W1:R3']['service_tier']=='default'
assert b['case_fallback']['W1:R3']['reasoning_effort']=='high'
assert b['case_fallback']['W1:R3']['provider_endpoint']=='google-vertex/global'
assert old['prompts']==new['prompts'] and old['contracts']==new['contracts']
other_model_changes=[k for k in b if k!='rounds' and a.get(k)!=b[k]];assert not other_model_changes,other_model_changes
since=datetime.fromtimestamp(before['cutover_at'],UTC).isoformat()
turns=[]
day=str(semantic_day(datetime.now(UTC)));rows=[]
tickers=[x[0] for x in db.execute('select distinct ticker from runtime_v2_cases')]
for ticker in tickers:
 cases=db.execute('select case_id from runtime_v2_cases where ticker=? and trading_date=? and created_at>=? order by created_at desc limit 15',(ticker,day,since)).fetchall()
 for case in cases:
  rows.extend(db.execute("select json_remove(payload_json,'$.output','$.raw_output') turn from runtime_v2_turns where case_id=?",(case[0],)).fetchall())
for row in rows:
 t=json.loads(row['turn'])
 turns.append({**{k:t.get(k) for k in ['case_id','lane','round_name','model','provider','reasoning_effort','service_tier','cache_ttl','status','error_code','input_tokens','cached_input_tokens','cache_write_input_tokens']},
  'actual_service_tier':(t.get('provider_usage') or {}).get('service_tier')})
policy_samples=[]
for t in turns:
 if t['lane']=='W2' and t['round_name']=='R2' and t['status']=='OK':
  f=value('round_inputs',t['case_id']+':W2:R2')
  policies=f['payload']['policy_details']['policies']
  assert all('transmission' not in x and 'match_scope' in x and all('trigger_layer' not in c for c in x['activation_conditions']) for x in policies)
  policy_samples.append(t['case_id'])
result={'checked_at':datetime.now(UTC).isoformat(),'active_execution':identity,'old_execution':before['active'],
 'changed_rounds':changed,'other_model_changes':other_model_changes,'prompts_unchanged':True,'contracts_unchanged':True,
 'rounds':b['rounds'],'w1r3_fallback':b['case_fallback']['W1:R3'],'natural_turns':turns,'natural_compact_policy_cases':policy_samples,
 'source_hashes':{m.__name__:hashlib.sha256(inspect.getsource(m).encode()).hexdigest() for m in [openai_platform,service,future_model_profiles]}}
print(json.dumps(result,ensure_ascii=False,indent=2))
