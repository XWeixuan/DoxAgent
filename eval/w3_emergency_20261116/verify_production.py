import hashlib,json,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,UTC
root=Path('/var/lib/doxagent/releases/w3-emergency-20261116');build=json.loads((root/'build.json').read_text());image=build['images']['backend']['image'];commit=build['commit']
paths=['src/doxagent/codex_worker/w3_ready.py','src/doxagent/persistent_runtime_v2/w3.py']
expected={p:hashlib.sha256(subprocess.check_output(['git','-C','/home/ubuntu/doxagent','show',commit+':'+p])).hexdigest() for p in paths}
code='import json,hashlib;from pathlib import Path;p='+repr(paths)+";print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in p}))"
out=dict(at=datetime.now(UTC).isoformat(),commit=commit,components={})
for role in ('codex-worker','v2-scheduler'):
 name='doxagent-v2-'+role+'-1';v=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
 assert v['State']['Running'] and v['Image']==image
 assert v['State'].get('Health',{}).get('Status','healthy')=='healthy'
 assert v['Config']['Labels']['org.opencontainers.image.revision']==commit
 assert json.loads(subprocess.check_output(['docker','exec',name,'python','-c',code]))==expected
 out['components'][role]=dict(image=image,running=True,health=v['State'].get('Health',{}).get('Status','running'))
data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
with sqlite3.connect((data/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2) as d:
 out['mode']=json.loads(d.execute("select payload from runtime_values where namespace='w3_trade_config' and key='mode'").fetchone()[0])
with sqlite3.connect((data/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2) as d:
 row=d.execute("select config from w3_ready_slots where state='READY' and json_extract(config,'$.w3_entry_version')='w3-entry-v6' order by ready_at desc limit 1").fetchone()
 assert row,'no READY v6 slot'
 Path('/home/ubuntu/w3-ready-canary-config.json').write_text(row[0])
out['hashes']=expected
(root/'verification.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
