import json,subprocess,hashlib
from pathlib import Path
s=json.loads(Path('/var/lib/doxagent/releases/state.json').read_bytes());print(json.dumps(s['components']['v2-projector']['current']))
for p in ['src/doxagent/v2_read/projector.py','src/doxagent/v2_read/cli.py']:
 raw=subprocess.check_output(['sudo','-u','ubuntu','git','-C','/home/ubuntu/doxagent','show','741043666:'+p]);print(p,hashlib.sha256(raw).hexdigest())
