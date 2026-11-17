import json,sqlite3,re
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
p=DoxAgentSettings().persistent_runtime_v2_sqlite_path
db=sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True,timeout=2);db.execute('pragma query_only=on');codec=NativeContent(p)
state=json.loads(Path('/tmp/cache-cost-live-20261117.json').read_text());out=[]
for r in state['receipts']:
 lane=r.get('lane','W1');rnd=r['round'];cid=r['case_id']
 f=codec.decode(json.loads(db.execute("select payload from runtime_values where namespace='round_inputs' and key=?",(cid+':'+lane+':'+rnd,)).fetchone()[0]))
 result={'case_id':cid,'lane':lane,'round':rnd,'arm':r['arm']}
 if rnd=='R1':
  text=f['payload']['published_known_event_index']+'\n'+f['payload']['today_provisional_facts']
  ids=set(re.findall(r'(?m)^\s*(E[0-9]+)\s*\|',text))
  result['references_in_frozen_input']=set(r['output']['event_ids'])<=ids
  assert result['references_in_frozen_input']
 if lane=='W2':
  old=codec.decode(json.loads(db.execute("select payload_json from runtime_v2_turns where case_id=? and lane='W2' and round_name='R2' and status='OK' order by attempt_number desc limit 1",(cid,)).fetchone()[0]))['output']
  fields=['policy_hit','policy_related','policy_ids','related_policy_ids','hit_condition_ids']
  result['historical_decision']={k:old.get(k) for k in fields};result['new_decision']={k:r['output'].get(k) for k in fields}
  result['agreement']=result['historical_decision']==result['new_decision']
 out.append(result)
print(json.dumps(out,ensure_ascii=False,indent=2))
