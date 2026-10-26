"""Stream every bounded keyset page instead of one large SQL/materialization."""
import gzip
import json
import sqlite3
import sys
import time
from doxagent.settings import DoxAgentSettings

mode,since,until=sys.argv[1:4]
db=sqlite3.connect('file:'+DoxAgentSettings().message_bus_v2_sqlite_path+'?mode=ro',uri=True,timeout=2)
db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
deadline=0;db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
table,clock,identity=('poll_runs','dispatch_at','run_id') if mode=='runs' else ('poll_captures','observed_at','capture_id')
columns='*' if mode=='runs' else 'capture_id,run_id,binding_id,source_id,ticker,unit,observed_at,committed_at,status,intake_position,message_count,first_intake_at,completed_at,last_error,request_started_at,source_version,binding_version,targets_json'
count=0;last=(since,'');pages=0
with gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb') as compressed:
    compressed.write((json.dumps(dict(kind='window',mode=mode,since=since,until=until))+'\n').encode())
    while True:
        deadline=time.monotonic()+10
        rows=db.execute('SELECT '+columns+' FROM '+table+' INDEXED BY '+table+'_time WHERE '+clock+'>=? AND ('+clock+','+identity+')>(?,?) AND '+clock+'<=?'+
            (" AND status!='SCHEDULE'" if mode=='runs' else '')+' ORDER BY '+clock+','+identity+' LIMIT 1000',(last[0],*last,until)).fetchall()
        if not rows:break
        for raw in rows:
            row=dict(raw)
            if mode=='runs':
                value=json.loads(row.pop('data_json'))
                row.update({k:v for k,v in value.items() if k in ('requests','captures','stages_ms','shared_run_id','required','unit_role','transport_plan','endpoint_key','operation_id','error_type','retry_not_before')})
            compressed.write((json.dumps(row,ensure_ascii=False)+'\n').encode())
        count+=len(rows);pages+=1;last=(rows[-1][clock],rows[-1][identity])
        if pages%10==0:print(mode,count,last[0],file=sys.stderr,flush=True)
    compressed.write((json.dumps(dict(kind='complete',rows=count,pages=pages))+'\n').encode())
db.close()
