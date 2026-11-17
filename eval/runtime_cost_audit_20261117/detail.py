import json,sqlite3,hashlib,collections
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
path=DoxAgentSettings().persistent_runtime_v2_sqlite_path
db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2);db.execute('pragma query_only=on')
codec=NativeContent(path);result=[];previous={}
def h(s):return hashlib.sha256(s.encode()).hexdigest()
for t in json.load(open('/tmp/runtime-cost-selected-20261117.json')):
 cid=t['case_id'];row=db.execute("select payload from runtime_values where namespace='round_inputs' and key=?",(cid+':W1:R1',)).fetchone()
 if not row:result.append({'case_id':cid,'missing':True});continue
 try:
  f=codec.decode(json.loads(row[0]));p=f['payload'];view=p['today_provisional_facts'];lines=view.splitlines();known=p.get('published_known_event_index','')
  key=(t['day'],t['ticker']);old=previous.get(key,[]);common=0
  for a,b in zip(old,lines):
   if a!=b:break
   common+=1
  result.append({**{k:t[k] for k in ['case_id','day','ticker','started_at','input_tokens','cached_input_tokens','cache_write_input_tokens','prefix_fingerprint']},'instructions_hash':h(f['instructions']),'known_hash':h(known),'known_chars':len(known),'known_lines':len(known.splitlines()),'provisional_chars':len(view),'provisional_lines':len(lines),'common_lines_previous_sample':common,'previous_sample_lines':len(old),'prefix_unchanged':lines[:len(old)]==old,'rows_head':lines[:2],'rows_tail':lines[-2:]})
  previous[key]=lines
 except Exception as e:result.append({'case_id':cid,'error':str(e)[:300]})
print(json.dumps(result,ensure_ascii=False))
