"""Supply the controller's missing Docker client, preserving the running configuration."""
import json
from pathlib import Path
from deploy_p4 import WORK, inspect, run, signature

before=inspect('doxagent-v2-initialization-guardian-1')
assert before['Config']['Image']=='doxagent-v2:disk-repair-guardian-20261013-r1'
dockerfile=WORK/'Dockerfile.guardian-cli'
dockerfile.write_text('FROM doxagent-v2:disk-repair-guardian-20261013-r1\n'
                      'COPY --from=docker:27.5-cli /usr/local/bin/docker /usr/local/bin/docker\n'
                      'COPY --from=docker:27.5-cli /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins\n')
image='doxagent-v2:disk-repair-guardian-20261013-r2'
print(run(['docker','build','-f',str(dockerfile),'-t',image,str(WORK/'p4-context')]),flush=True)
print(run(['docker','run','--rm','--network','none','--entrypoint','docker',image,'--version']),flush=True)
override=WORK/'v2-p4.yaml'
text=override.read_text()
assert 'disk-repair-guardian-20261013-r1' in text
override.write_text(text.replace('disk-repair-guardian-20261013-r1','disk-repair-guardian-20261013-r2'))
labels=before['Config']['Labels']
command=['docker','compose','-p','doxagent-v2']
for envfile in labels['com.docker.compose.project.environment_file'].split(','):
    command+=['--env-file',envfile]
for file in labels['com.docker.compose.project.config_files'].split(','):
    command+=['-f',file]
run(command+['config','--quiet'])
print(run(command+['up','-d','--no-build','--no-deps','initialization-guardian']),flush=True)
after=inspect('doxagent-v2-initialization-guardian-1')
assert signature(before)==signature(after)
path=WORK/'p4-deployment.json'
receipt=json.loads(path.read_text())
entry=receipt['services']['initialization-guardian']
entry.update(after_image=after['Image'],after_id=after['Id'],docker_cli_supplied=True)
path.write_text(json.dumps(receipt,indent=2))
print(json.dumps(dict(image=after['Image'],signature_preserved=True,docker_cli_supplied=True)),flush=True)
