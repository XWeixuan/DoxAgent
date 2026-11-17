import json,subprocess
from datetime import datetime,UTC
from pathlib import Path
out=dict(at=datetime.now(UTC).isoformat(),memory={k:v.strip() for k,v in (x.split(':',1) for x in Path('/proc/meminfo').read_text().splitlines()) if k in ('MemAvailable','SwapFree')},psi={x:Path('/proc/pressure/'+x).read_text() for x in ('io','memory','cpu')},roles={})
for role in ('v2-scheduler','codex-worker','v2-site-access','v2-chrome-supervisor'):
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0];pid=v['State']['Pid'];cg=Path('/sys/fs/cgroup'+Path('/proc/'+str(pid)+'/cgroup').read_text().strip().split('::')[-1])
 out['roles'][role]=dict(image=v['Image'],revision=v['Config'].get('Labels',{}).get('org.opencontainers.image.revision'),started=v['State']['StartedAt'],restarts=v['RestartCount'],oom=v['State']['OOMKilled'],memory_current=(cg/'memory.current').read_text(),memory_stat={k:int(a) for k,a in (x.split() for x in (cg/'memory.stat').read_text().splitlines()) if k in ('anon','file','swapcached')},io=(cg/'io.stat').read_text(),pids=(cg/'pids.current').read_text())
print(json.dumps(out))
