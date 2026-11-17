import sqlite3,json
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3')
ids=['037a839fcfcdc56899ff50e5b0559173e24b4033f6a099e9f09924168f9a2c1f','1d4654c06f9d41bfc7783cab9f188dcc49e4f7b51ac0b5750537a8101ac85dd1','cd08f1c93d31950f804e6a04c13da1f2d78aca78106ac0229948a407158b8078']
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
 out=[]
 for identity in ids:
  r=d.execute("select json_extract(payload,'$.resource_receipt.capsule_startup_timing'),json_extract(payload,'$.resource_receipt.cold_start_ms'),json_extract(payload,'$.resource_receipt.queue_wait_ms') from jobs where id=?",(identity,)).fetchone()
  out.append(dict(job_id=identity,startup=json.loads(r[0]) if r[0] else None,cold_start_ms=r[1],queue_wait_ms=r[2]))
print(json.dumps(out))
