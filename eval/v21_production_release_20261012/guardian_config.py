import json
from pathlib import Path
b=json.loads(Path('/home/ubuntu/v21-production-20261012/private-container-baseline.json').read_text())
x=b['initialization-guardian'];print('configs',x['Config']['Labels']['com.docker.compose.project.config_files']);print('mounts',[(m['Destination'],m['Source']) for m in x['Mounts']])
for f in x['Config']['Labels']['com.docker.compose.project.config_files'].split(','):
 for line in Path(f).read_text().splitlines():
  if ':?' in line:print(line)
