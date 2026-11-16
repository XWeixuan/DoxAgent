import json,sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
p=Path(DoxAgentSettings().persistent_runtime_v2_sqlite_path);d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2);d.row_factory=sqlite3.Row;content=NativeContent(p);out=[]
for r in d.execute("select c.case_id,c.payload_json from runtime_v2_cases c join runtime_v2_w3_stages s on s.case_id=c.case_id where s.phase='trade_decision' and s.status='COMMITTED' and s.accepted_at>='2026-11-16T11:06' order by s.committed_at desc limit 3"):
 v=content.decode(json.loads(r['payload_json']));consensus=v['w3_consensus'];assert consensus['mode']=='dual_consensus'
 assert v['frozen_inputs']['w3_trade_mode']=='dual_consensus'
 stages=[dict(x) for x in d.execute("select phase,thread_id,status from runtime_v2_w3_stages where case_id=?",(r['case_id'],))]
 threads={x['phase']:x['thread_id'] for x in stages};assert threads['trade_decision:A']!=threads['trade_decision:B'];assert threads['post_decision_operations']==threads['trade_decision:'+consensus['selected_branch']]
 out.append(dict(case_id=r['case_id'],decoded=True,selected=consensus['selected_branch'],directions_agree=consensus['directions_agree'],operations_uses_selected_thread=True))
print(json.dumps(out))
