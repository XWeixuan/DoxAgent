"""Read-only remote stream snapshot for the requested MU failure export."""
import json
import subprocess
from pathlib import Path

REMOTE = r'''
import json, sqlite3, os, inspect
from datetime import datetime, timezone, timedelta
db=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=10)
db.row_factory=sqlite3.Row
now=datetime.now(timezone.utc)
start=now-timedelta(days=3)
db.execute('begin')
rows=db.execute(''' + '"""' + '''
select si.stream_item_id,si.stream_offset,si.published_at as stream_published_at,
sm.member_index,std.data_json as standard_json,raw.data_json as raw_json
from stream_items si join stream_members sm on si.stream_item_id=sm.stream_item_id
join standard_messages std on std.standard_message_id=sm.standard_message_id
join raw_messages raw on raw.raw_message_id=std.raw_message_id
where si.ticker=? and julianday(si.published_at)>=julianday(?)
and julianday(si.published_at)<=julianday(?)
order by si.stream_offset,sm.member_index
''' + '"""' + ''',('MU',start.isoformat(),now.isoformat())).fetchall()
out={'window_start_utc':start.isoformat(),'window_end_utc':now.isoformat(),
'time_basis':'stream_items.published_at; rolling 72 hours','records':[]}
for row in rows:
 item=dict(row)
 item['standard']=json.loads(item.pop('standard_json'))
 item['raw']=json.loads(item.pop('raw_json'))
 out['records'].append(item)
for table,key,where in [('source_definitions','sources',''),
('ticker_source_bindings','bindings',"where ticker='MU'"),
('poll_states','poll_states',"where ticker='MU'")]:
 out[key]=[json.loads(r[0]) for r in db.execute('select data_json from '+table+' '+where)]
out['queue_columns']=[r[1] for r in db.execute('pragma table_info(content_enrichment_jobs)')]
out['queue_count']=db.execute('select count(*) from content_enrichment_jobs').fetchone()[0]
out['tables']=[r[0] for r in db.execute("select name from sqlite_master where type='table'")]
db.rollback();db.close()
out['settings']={k:v for k,v in os.environ.items() if k in (
'DOXAGENT_CONTENT_ENRICHMENT_ENABLED','DOXAGENT_MESSAGE_BUS_V2_CONTENT_ENRICHMENT_ENABLED',
'DOXAGENT_CONTENT_ENRICHMENT_PIPELINE_ENABLED','DOXAGENT_CONTENT_ENRICHMENT_BROWSER_ENABLED')}
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.content_enrichment.service import ContentEnrichmentHub
out['deployed_code']={'factory':inspect.getsource(build_message_bus_v2_service),
'accept_poll_result':inspect.getsource(MessageBusV2Service.accept_poll_result),
'hub_process':inspect.getsource(ContentEnrichmentHub._process)}
print(json.dumps(out,ensure_ascii=False))
'''

command=['ssh','-o','BatchMode=yes','-o','PasswordAuthentication=no','doxagent-sg',
         'sudo -n docker exec -i doxagent-v2-v2-message-bus-1 python -']
result=subprocess.run(command,input=REMOTE.encode('utf-8'),capture_output=True,timeout=60)
if result.returncode:
    raise SystemExit(result.stderr.decode('utf-8',errors='replace'))
snapshot=json.loads(result.stdout)
target=Path(__file__).parent/'remote_stream_snapshot.json'
target.write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'records':len(snapshot['records']),'start':snapshot['window_start_utc'],
                 'end':snapshot['window_end_utc'],'queue_count':snapshot['queue_count']},ensure_ascii=False))
