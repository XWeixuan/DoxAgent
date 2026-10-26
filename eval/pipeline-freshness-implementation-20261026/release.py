"""One release lock covers writer drain, additive Bus migration and targeted roll."""
import importlib.util
import json
import subprocess
from pathlib import Path

repo = Path('/home/ubuntu/doxagent')
root = Path('/var/lib/doxagent/releases')
commit = '85a97829b158fa99b877fada79d795aab6083aab'
spec = importlib.util.spec_from_file_location('release_ops',repo/'scripts/ops/storage_release.py')
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
components = ['v2-site-access','v2-message-bus','v2-content-enrichment']

with ops.lock(root):
    state = json.loads((root/'state.json').read_text())
    built = json.loads((root/(commit+'.build.json')).read_text())
    containers = ops.inspect_containers()
    old = {c['Config'].get('Labels',{}).get('com.docker.compose.service'):c['Id'] for c in containers}
    stopped = []
    try:
        for role in ('v2-message-bus','v2-content-enrichment'):
            subprocess.run(['docker','stop','-t','60',old[role]],check=True,capture_output=True)
            stopped.append(role)
        code = (
            "from doxagent.message_bus_v2.repository import MessageBusV2Repository;"
            "from doxagent.message_bus_v2.distribution_repository import DistributionRepository;"
            "from doxagent.v2_read.outbox import SourceOutbox;"
            "r=MessageBusV2Repository('/data/bus/bus.sqlite3');DistributionRepository(r);"
            "print(SourceOutbox('/data/bus/bus.sqlite3','bus').migrate())"
        )
        subprocess.run(['docker','run','--rm','--network','none','--volumes-from',old['v2-message-bus'],
                        '--entrypoint','python',built['images']['backend']['image'],'-c',code],check=True)
        result = ops.release_components(root,state,repo,commit,components)
        print(json.dumps({'commit':commit,'components':components,'result':result}))
    finally:
        # A migration/build failure must not leave otherwise healthy old writers stopped.
        current = ops.inspect_containers()
        for role in stopped:
            target = next((c for c in current if c['Config'].get('Labels',{}).get('com.docker.compose.service')==role),None)
            if target is not None and not target['State']['Running']:
                subprocess.run(['docker','start',target['Id']],check=True,capture_output=True)
