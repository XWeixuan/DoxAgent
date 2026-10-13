"""Exercise the real managed container creator with no business mounts or credentials."""
import json
import time
from doxagent.initialization_repair.containers import ContainerSpec, DockerRuntime
from doxagent.settings import DoxAgentSettings

runtime=DockerRuntime(DoxAgentSettings().initialization_repair_docker_executable)
name='disk-repair-log-smoke-20261013'
assert runtime.inspect(name) is None
spec=ContainerSpec(name=name,image='doxagent-v2:disk-repair-guardian-20261013-r1',
                   command=['python','-c','print("managed log rotation smoke")'],labels={})
runtime.ensure_started(spec)
try:
    state=runtime.inspect(name)
    logging=state['HostConfig']['LogConfig']
    assert logging=={'Type':'json-file','Config':{'max-size':'20m','max-file':'3'}}
    for _ in range(50):
        if runtime.state(name).status=='exited':
            break
        time.sleep(.1)
    assert runtime.state(name).exit_code==0
    print(json.dumps(dict(verified=True,logging=logging,business_mounts=[])),flush=True)
finally:
    runtime.remove(name)
