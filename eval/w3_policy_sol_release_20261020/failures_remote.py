import sqlite3,json
db=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True)
out=[]
for identity in ['case_c9378c7d671c41c5b0a3ada8f2262806','case_9d792e29998d4290ae7bc112cb63da12','case_a15841b16f7948f89c518fa2cddeb878']:
 r=db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(identity,)).fetchone()
 if r:
  c=json.loads(r[0]);item={k:c.get(k) for k in ['case_id','status','w3_error','w3_failure_phase','updated_at','errors']}
  item['stage']=db.execute("SELECT status,error FROM runtime_v2_w3_stages WHERE case_id=? AND phase='trade_decision'",(identity,)).fetchone();out.append(item)
  from doxagent.v2_read.native_content import NativeContent
  codec=NativeContent('/data/runtime/runtime.sqlite3')
  w2=codec.decode(c.get('w2_final')) or {};frozen=codec.decode(c.get('frozen_inputs',{}))
  item['profile']=frozen.get('w3_execution_profile');item['policy_hit']=w2.get('policy_hit');item['policy_related']=w2.get('policy_related')
print(json.dumps(out))
