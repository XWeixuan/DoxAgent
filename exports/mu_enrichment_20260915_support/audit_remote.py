import json
import subprocess
from pathlib import Path

folder=Path(__file__).parent
snapshot=json.loads((folder/'remote_stream_snapshot.json').read_text(encoding='utf-8'))
remote=r'''
import sqlite3,json,inspect,collections
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.schema import SourceDefinition
from doxagent.message_bus_v2.ibkr_news import IbkrNewsAdapter
settings=DoxAgentSettings()
out={'resolved_settings':{k:getattr(settings,k) for k in (
'content_enrichment_enabled','message_bus_v2_content_enrichment_enabled',
'content_enrichment_pipeline_enabled','content_enrichment_browser_enabled')},
'ibkr_poll_code':inspect.getsource(IbkrNewsAdapter.poll)}
db=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=10)
db.execute('begin')
out['resolved_source_modes']=[(s.source_id,s.content_enrichment_mode.value) for s in (
 SourceDefinition.model_validate_json(row[0]) for row in db.execute('select data_json from source_definitions'))]
rows=db.execute("select si.ticker,std.data_json from stream_items si join stream_members sm on si.stream_item_id=sm.stream_item_id join standard_messages std on std.standard_message_id=sm.standard_message_id where julianday(si.published_at)>=julianday(?) and julianday(si.published_at)<=julianday(?)",(START,END)).fetchall()
counts=collections.Counter();missing=[]
for ticker,text in rows:
 s=json.loads(text);m=s.get('metadata') or {};e=m.get('media_enrichment') or {}
 counts[(ticker,s['source_id'],e.get('status','missing'),e.get('reason') if e.get('status')=='skipped' else '')]+=1
 if not e:
  missing.append({'ticker':ticker,'source_id':s['source_id'],'standard_message_id':s['standard_message_id'],
  'title':s.get('title'),'url':s['url'],'body_length':len(s.get('body') or '')})
out['global_stream_counts']=[{'ticker':k[0],'source_id':k[1],'enrichment_status':k[2],'skip_reason':k[3],'count':v} for k,v in counts.items()]
out['missing_enrichment_records']=missing
db.rollback();db.close()
print(json.dumps(out,ensure_ascii=False))
'''
remote='START='+repr(snapshot['window_start_utc'])+'\nEND='+repr(snapshot['window_end_utc'])+'\n'+remote
result=subprocess.run(['ssh','-o','BatchMode=yes','-o','PasswordAuthentication=no','doxagent-sg',
'sudo -n docker exec -i doxagent-v2-v2-message-bus-1 python -'],input=remote.encode('utf-8'),capture_output=True,timeout=60)
if result.returncode: raise SystemExit(result.stderr.decode('utf-8',errors='replace'))
audit=json.loads(result.stdout)
(folder/'remote_queue_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in audit.items() if k!='ibkr_poll_code'},ensure_ascii=False,indent=2))
