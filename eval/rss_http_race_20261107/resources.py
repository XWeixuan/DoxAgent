import json,subprocess
from datetime import datetime,UTC
names=['doxagent-v2-'+n+'-1' for n in ['v2-message-collector','v2-message-bus','v2-content-enrichment','v2-api']]
rows=[json.loads(x) for x in subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}',*names],text=True).splitlines()]
print(json.dumps({'at':datetime.now(UTC).isoformat(),'containers':[{k:r[k] for k in ['Name','CPUPerc','MemUsage','PIDs']} for r in rows]}))
