"""Finite operator-approved release window; no realtime admission changes."""
from contextlib import closing
from datetime import UTC,datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

commit=sys.argv[1]
root=Path('/var/lib/doxagent/releases')/('w3-latency-'+commit[:12])
pause=json.loads(Path(sys.argv[2]).read_bytes())
result=dict(started_at=datetime.now(UTC).isoformat(),released=False,resumed=[])
deadline=time.monotonic()+1800
try:
    while True:
        with closing(sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True,timeout=.1)) as db:
            jobs=db.execute("SELECT status,count(*) FROM jobs WHERE status IN ('queued','running') GROUP BY status").fetchall()
            background=db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND json_extract(request,'$.node')!='persistent_runtime_w3' LIMIT 1").fetchone()
        print(json.dumps(dict(at=datetime.now(UTC).isoformat(),active_jobs=jobs)),flush=True)
        if not jobs:
            subprocess.run(['python3','/home/ubuntu/doxagent/scripts/ops/w3_latency_release_20261111.py',
                            'deploy','--commit',commit],check=True)
            result['released']=True;break
        if time.monotonic()>=deadline:raise TimeoutError('finite publication window ended with accepted work')
        time.sleep(30 if background else 3)
finally:
    for row in pause['containers']:
        if not row['was_running']:continue
        info=json.loads(subprocess.check_output(['docker','inspect',row['name']]))[0]
        if info['Id']!=row['id']:
            result['resumed'].append(dict(name=row['name'],result='container replaced by another publication; preserved'))
            continue
        if not info['State']['Running']:
            subprocess.run(['docker','start',row['id']],check=True)
        result['resumed'].append(dict(name=row['name'],result='running'))
    result['finished_at']=datetime.now(UTC).isoformat()
    (root/'publication-window.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
