import sqlite3,json,signal,os
from pathlib import Path
from doxagent.v2_read.native_content import NativeContent
os.nice(15);signal.alarm(60)
def db(path):
 c=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('pragma query_only=ON');return c
b=db('/data/bus/bus.sqlite3');d=db('/data/runtime/runtime.sqlite3');bc=NativeContent('/data/bus/bus.sqlite3');dc=NativeContent('/data/runtime/runtime.sqlite3')
terms=['Fermi does 600MW','Nebius Shares Drop 15%','PT Raised to $85 at Berenberg','Get Ready for What']
out=[];scanned=0
for r in b.execute("select * from standard_messages where published_at >= '2026-11-11' and published_at < '2026-11-14' and source_id in ('dcd_infrastructure_rss','huggingnews_rss','google_news_search_rss')"):
 scanned+=1;x=bc.decode(json.loads(r['data_json']));title=x.get('title') or x.get('snapshot',{}).get('title','')
 if not any(t.casefold() in title.casefold() for t in terms):continue
 v={k:r[k] for k in ['standard_message_id','ticker','source_id','published_at']};v['message']=x;v['cases']=[]
 for cr in d.execute('select case_id,payload_json from runtime_v2_cases where source_message_id=?',(r['standard_message_id'],)):
  c=dc.decode(json.loads(cr['payload_json']));wr=d.execute('select payload_json from runtime_v2_w3_cases where case_id=?',(cr['case_id'],)).fetchone();w=dc.decode(json.loads(wr[0])) if wr else None
  v['cases'].append({'case_id':cr['case_id'],'case':c,'w3':w})
 out.append(v)
print(json.dumps({'scanned':scanned,'matches':out},ensure_ascii=False))
