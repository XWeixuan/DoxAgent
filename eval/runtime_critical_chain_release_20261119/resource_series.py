"""Finite, low-rate resource acceptance; no application queries or new service."""
import json,subprocess,time
from pathlib import Path
from datetime import datetime,UTC
def read(p):
    try:return Path(p).read_text()
    except OSError:return ''
names=['doxagent-v2-'+r+'-1' for r in ('codex-worker','v2-scheduler','v2-projector','v2-transport-maintenance')]
roles={}
for c in json.loads(subprocess.check_output(['docker','inspect',*names])):
    pid=c['State']['Pid']
    cg=next(x.split(':',2)[2] for x in read(f'/proc/{pid}/cgroup').splitlines() if x.startswith('0::'))
    roles[c['Name']]=Path('/sys/fs/cgroup')/cg.lstrip('/')
samples=[]
for i in range(61):
    samples.append(dict(at=datetime.now(UTC).isoformat(),psi={k:read('/proc/pressure/'+k) for k in ('cpu','memory','io')},memory={line.split(':')[0]:line.split(':')[1].strip() for line in read('/proc/meminfo').splitlines() if line.startswith(('MemAvailable:','SwapFree:'))},swap={k:int(v) for k,v in (x.split() for x in read('/proc/vmstat').splitlines()) if k in ('pswpin','pswpout')},roles={r:{k:read(p/k) for k in ('memory.current','memory.events','cpu.stat','io.stat','pids.current')} for r,p in roles.items()}))
    if i<60:time.sleep(10)
print(json.dumps(samples))
