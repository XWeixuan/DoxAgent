import json,subprocess
from pathlib import Path
r=Path('/home/ubuntu/v21-production-20261012');b=json.loads((r/'private-container-baseline.json').read_text())
n='v2-initialization';p=dict(x.split('=',1) for x in b[n]['Config']['Env']);c=dict(x.split('=',1) for x in json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+n+'-1']))[0]['Config']['Env'])
print('added_keys',list(c.keys()-p.keys()),'removed_keys',list(p.keys()-c.keys()),'changed_keys',[k for k in p.keys()&c.keys() if p[k]!=c[k]])
