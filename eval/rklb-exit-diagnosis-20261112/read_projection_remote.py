import os,sqlite3,json
from pathlib import Path
from datetime import datetime,timezone
from doxagent.v2_read.repository import ReadStore
p=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']).path;d=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('pragma query_only=ON');d.execute('begin')
root='trade:case_f8319410bef746ca998e29279eaaa216';case='case_f8319410bef746ca998e29279eaaa216';out={'at':datetime.now(timezone.utc).isoformat(),'read_path':str(p)}
out['schema']={t:[dict(r) for r in d.execute('pragma table_info('+t+')')] for t in ['object_current','object_history','objects','batches','checkpoints','source_health','projection_batches','changes']}
out['records']=[dict(r) for r in d.execute("select kind,id,parent,valid_from,valid_to,payload from objects where ticker='RKLB' and ((kind in ('execution','native:te_executions') and id=?) or (kind='case' and id=?)) and valid_to is null",(root,case))]
for row in out['records']:row['payload']=json.loads(row['payload'])
out['checkpoints']=[dict(r) for r in d.execute("select * from checkpoints where source='runtime'")]
out['health']=[dict(r) for r in d.execute("select * from source_health where source='runtime'")]
out['tables']=[r[0] for r in d.execute("select name from sqlite_master where type='table' and (name like '%batch%' or name like '%meta%' or name like '%revision%')")]
print(json.dumps(out))
