import json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/v21-production-validation/workspaces/v21-deployment-maintain-smoke-3/context/shared')
print('context_files', [str(f.relative_to(p)) for f in p.rglob('*') if f.is_file()][:40] if p.exists() else 'pending')
r=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/v21-production-validation/model-smoke.json')
d=json.loads(r.read_text());print('maintain',d.get('maintain',{}).get('result',{}).get('run_id'),d.get('maintain',{}).get('result',{}).get('status'))
