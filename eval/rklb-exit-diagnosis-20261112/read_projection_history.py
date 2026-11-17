import os,sqlite3,json,re
from pathlib import Path
from datetime import datetime,timezone
from doxagent.v2_read.repository import ReadStore
p=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']).path;d=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('pragma query_only=ON');d.execute('begin')
root='trade:case_f8319410bef746ca998e29279eaaa216';case='case_f8319410bef746ca998e29279eaaa216'
out={'at':datetime.now(timezone.utc).isoformat(),'schemas':{t:[dict(r) for r in d.execute('pragma table_info('+t+')')] for t in ['read_meta','revisions','source_health','gaps']}}
out['meta']=[dict(r) for r in d.execute('select * from read_meta')]
out['history']=[{**dict(r),'payload':json.loads(r['payload'])} for r in d.execute("select kind,valid_from,valid_to,payload from objects where ticker='RKLB' and kind in ('execution','case') and id in (?,?) order by valid_from limit 45",(root,case))]
out['gaps']=[dict(r) for r in d.execute("select * from gaps where source='runtime' limit 12")]
# Source table schema and only indexed target-entity events.
s=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True);s.row_factory=sqlite3.Row;s.execute('pragma query_only=ON')
out['outbox_schema']=[dict(r) for r in s.execute('pragma table_info(v2_source_outbox)')]
print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b',r'\1\2***\3',json.dumps(out)))
