import json
import subprocess
from datetime import datetime, timezone

out={'at':datetime.now(timezone.utc).isoformat()}
for key,cmd in [('processes',['ps','-p','2172273,3092140,2200385','-o','pid,rss,vsz,pcpu,etime,comm']),('disk',['df','-h','/'])]:
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
    out[key]={'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
print(json.dumps(out))
