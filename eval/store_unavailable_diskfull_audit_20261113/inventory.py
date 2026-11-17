import json,subprocess,os,shutil
from pathlib import Path
from datetime import datetime,timezone
names=subprocess.check_output(['docker','ps','-a','--format','{{.Names}}'],text=True).splitlines();containers=[]
for j in json.loads(subprocess.check_output(['docker','inspect',*names])):
 s=j['State'];h=s.get('Health',{})
 containers.append(dict(name=j['Name'].lstrip('/'),image=j['Image'],status=s['Status'],oom=s['OOMKilled'],exit=s['ExitCode'],error=s['Error'],started=s['StartedAt'],finished=s['FinishedAt'],restarts=j['RestartCount'],health=h.get('Status'),health_failures=h.get('FailingStreak'),health_log=[dict(at=x['End'],exit=x['ExitCode'],output=x.get('Output','')[:700]) for x in h.get('Log',[])[-2:]]))
r=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}'],capture_output=True,text=True,timeout=15)
stats=[json.loads(l) for l in r.stdout.splitlines()]
out=dict(at=datetime.now(timezone.utc).isoformat(),disk=shutil.disk_usage('/')._asdict(),containers=containers,stats=stats,load=Path('/proc/loadavg').read_text(),uptime=Path('/proc/uptime').read_text(),meminfo=Path('/proc/meminfo').read_text(),psi={n:Path('/proc/pressure/'+n).read_text() for n in ['cpu','io','memory']})
print(json.dumps(out))
