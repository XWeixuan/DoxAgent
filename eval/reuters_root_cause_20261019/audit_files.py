"""Read-only exact deployed-file receipt; no environment or credential output."""
import json,subprocess
files=['site_strategy/worker_runtime.py','site_strategy/identity_worker.py',
       'site_strategy/runtime.py','site_strategy/service.py','site_strategy/maintenance.py',
       'site_strategy/maintenance_document.py','content_enrichment/quality.py']
code="import hashlib,json;from pathlib import Path;p=Path('/app/src/doxagent');print(json.dumps({f:hashlib.sha256((p/f).read_bytes()).hexdigest() for f in "+repr(files)+"}))"
hashes=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-v2-site-access-1','python','-c',code],text=True))
c=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-site-access-1','doxagent-v2-v2-chrome-supervisor-1'],text=True))
print(json.dumps({'hashes':hashes,'site_image':c[0]['Image'],'supervisor_id':c[1]['Id'],
 'same_network_namespace':subprocess.check_output(['readlink',f"/proc/{c[0]['State']['Pid']}/ns/net"],text=True).strip()==subprocess.check_output(['readlink',f"/proc/{c[1]['State']['Pid']}/ns/net"],text=True).strip()}))
