"""Finite, read-only release observation; no model requests or mutations."""
import argparse
from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import time

ROOT=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')

def snapshot():
    result={'at':datetime.now(UTC).isoformat()}
    result['memory_kib']={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,*value=line.split()
        if key.rstrip(':') in {'MemAvailable','SwapFree','SwapTotal'}:
            result['memory_kib'][key.rstrip(':')]=int(value[0])
    result['vmstat']={k:int(v) for k,v in (line.split() for line in Path('/proc/vmstat').read_text().splitlines())
                      if k in {'pswpin','pswpout','pgmajfault','oom_kill'}}
    result['pressure']={name:Path('/proc/pressure/'+name).read_text().strip() for name in ['memory','io','cpu']}
    result['roles']={}
    for role in ['v2-scheduler','codex-worker']:
        try:
            info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
            item={'image':info['Image'],'running':info['State']['Running'],'oom_killed':info['State']['OOMKilled'],
                  'restarts':info['RestartCount'],'started_at':info['State']['StartedAt']}
            if info['State']['Pid']:
                group=Path('/proc/'+str(info['State']['Pid'])+'/cgroup').read_text().split('::',1)[1].strip()
                cg=Path('/sys/fs/cgroup'+group)
                for key in ['memory.current','memory.swap.current','pids.current','memory.events','io.stat']:
                    p=cg/key
                    if p.exists():item[key]=p.read_text().strip()
            result['roles'][role]=item
        except Exception as exc:result['roles'][role]={'error':type(exc).__name__}
    connect=lambda p:sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,timeout=1)
    try:
        with closing(connect(ROOT/'workspaces/worker-jobs.sqlite3')) as db:
            result['active_jobs']=dict(db.execute("SELECT status,count(*) FROM jobs WHERE status IN ('queued','running') GROUP BY status"))
            result['recent_jobs']=[dict(sequence=r[0],job_id=r[1],status=r[2],phase=r[3],error_code=r[4]) for r in db.execute(
                "SELECT sequence,id,status,json_extract(request,'$.task_phase'),json_extract(payload,'$.error_code') FROM jobs ORDER BY sequence DESC LIMIT 30")]
        with closing(connect(ROOT/'runtime/runtime.sqlite3')) as db:
            row=db.execute("SELECT payload FROM runtime_values WHERE namespace='w3_provider_pause' AND key='codex'").fetchone()
            result['pause']=json.loads(row[0]) if row else None
            cases=db.execute('SELECT case_id,status,technical_status FROM runtime_v2_cases ORDER BY rowid DESC LIMIT 200').fetchall()
            ids=[r[0] for r in cases]
            result['recent_case_status']={}
            for _,status,technical in cases:
                key=status+'/'+technical
                result['recent_case_status'][key]=result['recent_case_status'].get(key,0)+1
            if ids:
                marks=','.join('?' for _ in ids)
                result['w3_effect_status']=[dict(effect_type=t,status=s,count=n) for t,s,n in db.execute(
                    "SELECT effect_type,status,count(*) FROM runtime_v2_effects WHERE case_id IN ("+marks+") AND effect_type IN ('route_to_w3','w3_post_decision') GROUP BY effect_type,status",ids)]
    except sqlite3.Error as exc:result['db_sample_error']=type(exc).__name__
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=600);p.add_argument('--interval',type=int,default=20)
    args=p.parse_args();started=time.monotonic()
    while True:
        print(json.dumps(snapshot(),separators=(',',':')),flush=True)
        remaining=args.seconds-(time.monotonic()-started)
        if remaining<=0:break
        time.sleep(min(args.interval,remaining))
