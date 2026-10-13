"""Deploy only the measured Policy-hit lookup optimization to the projector."""
import json
import subprocess
from pathlib import Path

WORK=Path('/home/ubuntu/disk-repair-20261013')
name='doxagent-v2-v2-projector-1'
before=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
context=WORK/'lookup-context'
context.mkdir(exist_ok=True)
(context/'Dockerfile').write_text('FROM doxagent-v2:disk-repair-read-20261013-r9\n'
    'COPY policy_hits.py /app/src/doxagent/v2_read/policy_hits.py\n')
image='doxagent-v2:disk-repair-read-20261013-r10'
subprocess.run(['docker','build','-t',image,str(context)],check=True)
labels=before['Config']['Labels']
command=['docker','compose','-p','doxagent-v2']
for env in labels['com.docker.compose.project.environment_file'].split(','):
    command+=['--env-file',env]
for file in dict.fromkeys(labels['com.docker.compose.project.config_files'].split(',')):
    command+=['-f',file]
override=WORK/'v2-projector-lookup.yaml'
override.write_text('services:\n  v2-projector:\n    image: '+image+'\n')
command+=['-f',str(override)]
subprocess.run(command+['up','-d','--no-deps','--no-build','v2-projector'],check=True)
after=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
from deploy_p4 import signature
assert signature(before)==signature(after)
receipt=dict(before_image=before['Image'],image=after['Image'],config_preserved=True)
(WORK/'projector-lookup-deployment.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
