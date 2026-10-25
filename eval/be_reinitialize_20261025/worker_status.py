import json, sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
s=DoxAgentSettings(); repo=InitializationRepository('/data/ops/be-refresh-20261025/control.sqlite3'); run='init-be-a79840f3bcbf44218b49a1aceba5e6a3'
ids=[run,run+'-d1',run+'-d2']
for n in repo.nodes(run):
    for k in n.key.split(':'):
        if k.startswith('d2ws-'):ids.append(k)
p=Path(s.codex_workspace_root)/'worker-jobs.sqlite3'
if not p.exists(): p=Path('/data/workspaces/worker-jobs.sqlite3')
with sqlite3.connect(f'file:{p}?mode=ro',uri=True) as db:
    rows=db.execute('select id,status,request,payload from jobs where '+ ' OR '.join('request like ?' for _ in ids),['%'+i+'%' for i in ids]).fetchall()
result=[]
for jid,status,req,payload in rows:
    q=json.loads(req or '{}'); j=json.loads(payload)
    result.append({'id':jid,'status':status,'node':q.get('node'),'model':q.get('model'),'effort':q.get('effort'),'error':j.get('error'),'run_id':q.get('run_id')})
print(json.dumps(result,ensure_ascii=False))
