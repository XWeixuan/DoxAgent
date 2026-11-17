import json,sqlite3,collections,time
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
path=DoxAgentSettings().persistent_runtime_v2_sqlite_path
db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row;db.execute('pragma query_only=on')
codec=NativeContent(path);r={}
for day in ['2026-11-11','2026-11-13','2026-11-16']:
 cs=list(db.execute("""select case_id,ticker,json_extract(payload_json,'$.w1_final.result') novelty,
 json_extract(payload_json,'$.w3_contract_version') contract,json_extract(payload_json,'$.runtime_mode') mode,
 json_extract(payload_json,'$.source.logical_message_id') logical_id,
 json_extract(payload_json,'$.source.update_kind') update_kind,
 json_extract(payload_json,'$.route.primary_route') route,
 json_extract(payload_json,'$.route.side_effects') effects
 from runtime_v2_cases where trading_date=?""",(day,)))
 counts=collections.Counter((c['ticker'],c['logical_id']) for c in cs if c['logical_id'])
 d={'cases':len(cs),'novelty':dict(collections.Counter(c['novelty'] for c in cs)),'contract':dict(collections.Counter(c['contract'] for c in cs)),'route':dict(collections.Counter(c['route'] for c in cs)),'effects':dict(collections.Counter(c['effects'] for c in cs)),'update_kind':dict(collections.Counter(c['update_kind'] for c in cs)),'ticker_logical_duplicates':sum(n-1 for n in counts.values()),'r3_outputs':{}}
 n=collections.Counter();candidates=0
 for c in cs:
  row=db.execute("select payload_json from runtime_v2_turns where case_id=? and lane='W1' and round_name='R3' and status='OK' order by attempt_number desc limit 1",(c['case_id'],)).fetchone()
  if row:
   turn=json.loads(row[0]);out=codec.decode(turn.get('output') or {});l=len(out.get('candidates',[]));n['calls']+=1;n['empty' if l==0 else 'nonempty']+=1;candidates+=l
 d['r3_outputs']={**n,'candidates':candidates}
 d['candidate_schema']=[x[1] for x in db.execute('pragma table_info(runtime_v2_candidates)')]
 d['candidate_origins']=list(map(dict,db.execute("""select ticker,json_extract(payload_json,'$.originating_node') origin,
 case when source_message_id like 'library-bridge:%' then 'bridge' else 'message' end source_type,
 count(*) n from runtime_v2_candidates where trading_date=? group by 1,2,3""",(day,))))
 r[day]=d
print(json.dumps(r,ensure_ascii=False))
