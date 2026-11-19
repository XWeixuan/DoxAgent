import json,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,UTC
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out=dict(at=datetime.now(UTC).isoformat())
with sqlite3.connect((root/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
    db.row_factory=sqlite3.Row
    out['jobs']=[dict(r) for r in db.execute("SELECT id,status,json_extract(request,'$.node') node,json_extract(request,'$.task_phase') phase,json_extract(payload,'$.created_at') created FROM jobs WHERE status IN ('queued','running') LIMIT 64")]
    out['slots']=[dict(r) for r in db.execute('SELECT scope,state,count(*) n FROM w3_ready_slots WHERE state IN (\'READY\',\'BUILDING\') GROUP BY scope,state')]
out['memory']={line.split(':')[0]:line.split(':')[1].strip() for line in Path('/proc/meminfo').read_text().splitlines() if line.split(':')[0] in ('MemAvailable','SwapFree','SwapTotal')}
out['psi']={k:Path('/proc/pressure/'+k).read_text().strip() for k in ('cpu','memory','io')}
roles=('v2-scheduler','codex-worker','v2-projector','v2-transport-maintenance','v2-api','v2-executor','v2-maintenance','v2-initialization')
infos=json.loads(subprocess.check_output(['docker','inspect',*['doxagent-v2-'+r+'-1' for r in roles]],text=True))
out['services']=[dict(name=i['Name'],image=i['Image'],started=i['State']['StartedAt'],running=i['State']['Running'],health=i['State'].get('Health',{}).get('Status'),revision=next((v.split('=',1)[1] for v in i['Config']['Env'] if v.startswith('DOXAGENT_BUILD_COMMIT=')),None),ready_target=next((v.split('=',1)[1] for v in i['Config']['Env'] if v.startswith('DOXAGENT_W3_READY_LUNA=')),None)) for i in infos]
print(json.dumps(out))
