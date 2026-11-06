import json,subprocess
from datetime import datetime,UTC
names=['doxagent-v2-'+n+'-1' for n in ['v2-message-collector','v2-message-bus','v2-content-enrichment','v2-api']]
rows=json.loads(subprocess.check_output(['docker','inspect',*names]))
print(json.dumps({'at':datetime.now(UTC).isoformat(),'containers':[{'name':r['Name'],'state':r['State']['Status'],'health':r['State'].get('Health',{}).get('Status'),'restarts':r['RestartCount'],'commit':dict(x.split('=',1) for x in r['Config']['Env']).get('DOXAGENT_BUILD_COMMIT')} for r in rows]}))
