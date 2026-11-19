"""Six bounded nonblocking stack snapshots; never suspend or signal the owner."""
import json,subprocess,time
from datetime import datetime,UTC
from pathlib import Path
info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-scheduler-1']))[0]
init=info['State']['Pid']
group=next(x.split(':',2)[2] for x in Path(f'/proc/{init}/cgroup').read_text().splitlines() if x.startswith('0::'))
pids=(Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs').read_text().split()
pid=next((int(p) for p in pids if b'doxagent.runtime_scheduler' in Path(f'/proc/{p}/cmdline').read_bytes()),None)
if pid is None:raise RuntimeError('Scheduler Python owner unavailable')
rows=[]
for i in range(6):
    began=time.monotonic()
    r=subprocess.run(['/home/ubuntu/runtime-root-pyspy','dump','--pid',str(pid),'--nonblocking','--json'],capture_output=True,text=True,timeout=2)
    value=None
    if r.returncode==0:
        try:value=json.loads(r.stdout)
        except ValueError:pass
    rows.append(dict(at=datetime.now(UTC).isoformat(),seconds=time.monotonic()-began,code=r.returncode,stacks=value,error=r.stderr[:200] if r.returncode else None))
    if i<5:time.sleep(1)
print(json.dumps(rows))
