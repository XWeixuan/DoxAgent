import json,sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings()
paths={k:str(v) for k,v in s.model_dump().items() if ('path' in k or 'database' in k) and ('runtime' in k or 'read' in k)}
print('PATHS',json.dumps(paths))
p=s.persistent_runtime_v2_sqlite_path
db=sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
db.execute('pragma query_only=on')
db.set_progress_handler(lambda: 0,100000)
print('TABLES',db.execute("select name from sqlite_master where type='table' and (name like '%turn%' or name like '%case%' or name like '%content%')").fetchall())
for table in ['runtime_v2_turns','runtime_v2_cases']:
 print('SCHEMA',table,db.execute('pragma table_info('+table+')').fetchall())
 row=db.execute('select payload_json from '+table+' order by rowid desc limit 1').fetchone()
 d=json.loads(row[0]);print('KEYS',table,list(d))
 if table.endswith('turns'):print('SAMPLE',json.dumps({k:v for k,v in d.items() if k not in ['input','output','instructions','raw_output','rendered_input','prompt','replay_parent']},default=str)[:14000])
 else:print('CASE_SHAPE',json.dumps({k:(list(v) if isinstance(v,dict) else str(v)[:150]) for k,v in d.items()}))
