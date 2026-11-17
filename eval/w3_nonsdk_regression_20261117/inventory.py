import json,hashlib,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,UTC
out=dict(at=datetime.now(UTC).isoformat(),roles={})
paths=['src/doxagent/v2_read/decision_metrics.py','src/doxagent/v2_read/native_content.py','src/doxagent/persistent_runtime_v2/w3.py','src/doxagent/persistent_runtime_v2/w3_execution.py','src/doxagent/persistent_runtime_v2/worker_receipts.py','src/doxagent/codex_worker/w3_ready.py','src/doxagent/codex_worker/sdk_runtime.py']
code='import hashlib,json;from pathlib import Path;p='+repr(paths)+";print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in p}))"
for role in ('v2-scheduler','codex-worker','v2-projector','v2-api'):
 name='doxagent-v2-'+role+'-1';v=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
 out['roles'][role]=dict(image=v['Image'],revision=v['Config'].get('Labels',{}).get('org.opencontainers.image.revision'),started=v['State']['StartedAt'],restarts=v['RestartCount'],oom=v['State']['OOMKilled'],hashes=json.loads(subprocess.check_output(['docker','exec',name,'python','-c',code])))
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3');db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2)
out['tables']=[r[0] for r in db.execute("select name from sqlite_master where type='table' and (name like '%attempt%' or name like '%invocation%')")]
out['mode']=db.execute("select payload from runtime_values where namespace='w3_trade_config' and key='mode'").fetchone()
out['memory']={k:v.strip() for k,v in (x.split(':',1) for x in Path('/proc/meminfo').read_text().splitlines()) if k in ('MemAvailable','SwapFree')}
print(json.dumps(out))
