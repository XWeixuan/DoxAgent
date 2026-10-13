"""Refresh only the repaired Bus overlay, without enabling maintenance hooks."""
import json,subprocess
from pathlib import Path
root=Path('/home/ubuntu/source-repair-20261012')
release=json.loads((root/'release.json').read_text())
args=['docker','compose','-p','doxagent-v2','--env-file','/home/ubuntu/doxagent/.env.v2']
for path in release['compose_files']+[release['overlay']]:args+=['-f',path]
subprocess.run(['docker','stop','-t','100','doxagent-v2-v2-message-bus-1'],check=True,timeout=110)
subprocess.run(args+['up','-d','--no-deps','--no-build','v2-message-bus'],check=True,timeout=120)
v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-message-bus-1']))[0]
release['after']['doxagent-v2-v2-message-bus-1']={'id':v['Id'],'image':v['Image'],'tag':v['Config']['Image'],'started':v['State']['StartedAt']}
(root/'release.json').write_text(json.dumps(release,indent=2))
print(json.dumps(release['after']['doxagent-v2-v2-message-bus-1']))
