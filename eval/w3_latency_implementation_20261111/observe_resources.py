"""Read cgroup/proc counters and indexed active Job counts; no inventory scans."""
from contextlib import closing
from datetime import UTC,datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


def snapshot():
    value=dict(at=datetime.now(UTC).isoformat(),memory={},psi={},roles={})
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,raw=line.split(':',1)
        if key in ['MemAvailable','SwapFree','SwapTotal']:value['memory'][key]=int(raw.split()[0])
    value['psi']={k:Path('/proc/pressure',k).read_text().strip() for k in ['cpu','io','memory']}
    value['vmstat']={p[0]:int(p[1]) for line in Path('/proc/vmstat').read_text().splitlines()
                     if (p:=line.split())[0] in ['pswpin','pswpout','oom_kill']}
    infos=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-scheduler-1','doxagent-v2-codex-worker-1']))
    for role,d in zip(['v2-scheduler','codex-worker'],infos):
        row=dict(image=d['Image'],started_at=d['State']['StartedAt'],running=d['State']['Running'],
                 restarts=d['RestartCount'],oom=d['State']['OOMKilled'])
        if row['running']:
            cg=Path('/sys/fs/cgroup')/Path('/proc',str(d['State']['Pid']),'cgroup').read_text().strip().split('::')[-1].lstrip('/')
            row['counters']={k:(cg/k).read_text().strip() for k in ['memory.current','memory.swap.current','memory.events','cpu.stat','io.stat','pids.current']}
        value['roles'][role]=row
    with closing(sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True,timeout=.1)) as db:
        value['jobs']=db.execute("SELECT status,count(*) FROM jobs WHERE status IN ('queued','running') GROUP BY status").fetchall()
    return value


if __name__=='__main__':
    output=Path(sys.argv[1]);duration=float(sys.argv[2]) if len(sys.argv)>2 else 600
    started=time.monotonic()
    with output.open('w') as out:
        while True:
            try:value=snapshot()
            except Exception as exc:value=dict(at=datetime.now(UTC).isoformat(),error=type(exc).__name__)
            value['elapsed_s']=round(time.monotonic()-started,3)
            out.write(json.dumps(value)+'\n');out.flush()
            print(json.dumps({k:value[k] for k in ['at','memory','jobs','error'] if k in value}),flush=True)
            remaining=duration-(time.monotonic()-started)
            if remaining<=0:break
            time.sleep(min(15,remaining))
