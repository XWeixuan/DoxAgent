import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

names = ["doxagent-v2-v2-projector-1", "doxagent-v2-v2-maintenance-1", "doxagent-v2-v2-scheduler-1", "doxagent-v2-v2-transport-maintenance-1"]
items = []
for name in names:
    d = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
    pid = d["State"]["Pid"]
    cg = Path("/proc",str(pid),"cgroup").read_text().split("::",1)[1].strip()
    items.append((name,pid,Path("/sys/fs/cgroup"+cg)))
print(json.dumps({"meta": [(n,p,str(c)) for n,p,c in items]}),flush=True)
for index in range(121):
    if index % 4 == 0:
        items=[]
        for name in names:
            d=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
            pid=d['State']['Pid']
            cg=Path('/proc',str(pid),'cgroup').read_text().split('::',1)[1].strip()
            items.append((name,pid,Path('/sys/fs/cgroup'+cg)))
    out={"at":datetime.now(timezone.utc).isoformat(),"index":index,"processes":{}}
    for name,pid,cg in items:
        d={"pid":pid}
        try:
            d['rss_bytes']=sum(int(line.split()[1])*1024 for proc in (cg/'cgroup.procs').read_text().split() for line in Path('/proc',proc,'status').read_text().splitlines() if line.startswith('VmRSS:'))
        except OSError:pass
        for f in ("io","stat","schedstat","wchan","syscall"):
            try:d[f]=Path("/proc",str(pid),f).read_text()
            except OSError as exc:d[f]=str(exc)
        for f in ("io.stat","cpu.stat","memory.current","memory.stat","memory.events","io.pressure","cpu.pressure"):
            try:d[f]= (cg/f).read_text()
            except OSError:pass
        out["processes"][name]=d
    for f in ("diskstats","stat","vmstat","pressure/io","pressure/cpu","meminfo"):
        out[f]=Path("/proc",f).read_text()
    print(json.dumps(out),flush=True)
    if index<120:time.sleep(15)
