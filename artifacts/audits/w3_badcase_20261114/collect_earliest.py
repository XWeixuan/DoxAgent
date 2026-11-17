import sqlite3,json,signal,os,re
from doxagent.v2_read.native_content import NativeContent
os.nice(15);signal.alarm(90)
def db(path):
 c=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('pragma query_only=ON');return c
b=db('/data/bus/bus.sqlite3');d=db('/data/runtime/runtime.sqlite3');bc=NativeContent('/data/bus/bus.sqlite3');dc=NativeContent('/data/runtime/runtime.sqlite3')
out=[];count=0
for ticker,start in [('RKLB','2026-11-08'),('BE','2026-11-11'),('COHR','2026-11-11'),('NBIS','2026-11-11')]:
 for r in b.execute('select * from standard_messages where ticker=? and published_at>=? and published_at<?',(ticker,start,'2026-11-14')):
  count+=1;x=bc.decode(json.loads(r['data_json']));title=x.get('title') or x.get('snapshot',{}).get('title','');body=x.get('body') or x.get('snapshot',{}).get('body','')
  pattern=r'Morgan Stanley|Berenberg|Liwag' if ticker=='RKLB' else (r'Fermi|TensorWave|Nebius|Shareholder|Third.Quarter' if ticker in ('BE','COHR') else r'Nebius.*(Third|Results|Outlook|Letter|Revenues|Revenue|Delays)|Shareholder|DataOne')
  if not re.search(pattern,title,re.I):continue
  v={k:r[k] for k in ['standard_message_id','ticker','source_id','published_at']};v['message']=x;v['cases']=[]
  for cr in d.execute('select case_id,payload_json from runtime_v2_cases where source_message_id=?',(r['standard_message_id'],)):
   c=dc.decode(json.loads(cr['payload_json']));v['cases'].append({k:c.get(k) for k in ['case_id','created_at','source','w1_final','w2_final','w3_trade_decision']})
  out.append(v)
print(json.dumps({'scanned':count,'matches':out},ensure_ascii=False))
