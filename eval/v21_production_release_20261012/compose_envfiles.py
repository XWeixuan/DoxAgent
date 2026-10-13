import json,shutil,os
from pathlib import Path
b=json.loads(Path('/home/ubuntu/v21-production-20261012/private-container-baseline.json').read_text())
for x in b.values():
 f=x['Config']['Labels']['com.docker.compose.project.config_files'].split(',')[0]
 p=Path(f).parent/'.env.v2'
 if not p.exists():
  assert p.parent.is_dir() and p.name=='.env.v2'
  shutil.copyfile('/home/ubuntu/doxagent/.env.v2',p);p.chmod(0o600);print('restored historical compose environment file',p)
