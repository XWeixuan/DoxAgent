import json
from pathlib import Path
root=Path('/var/lib/doxagent/releases/w3-emergency-20261116');dest=Path('/home/ubuntu')
for name in ('verification.json','candidate-verification.json','observations.jsonl'):
 p=dest/('w3-emergency-final-'+name);p.write_bytes((root/name).read_bytes());p.chmod(0o644)
v=json.loads((root/'deploy.json').read_text());p=dest/'w3-emergency-final-deploy-summary.json';p.write_text(json.dumps({k:v.get(k) for k in ('errors','pressure','available_after')},indent=2));p.chmod(0o644)
