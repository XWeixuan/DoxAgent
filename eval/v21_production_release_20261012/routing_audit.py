import json
from pathlib import Path
root=Path('/home/ubuntu/v21-production-20261012')
x=json.loads((root/'private-container-baseline.json').read_text())
for name in ('codex-worker','v2-initialization','v2-scheduler'):
 env=dict(s.split('=',1) for s in x[name]['Config']['Env'])
 print(name,{k:v for k,v in env.items() if any(z in k for z in ('ORCHESTRATION','MODEL','EFFORT','CONTAINER_ISOLATION')) and not any(z in k for z in ('KEY','TOKEN','SECRET'))})
