import os,subprocess,json
from pathlib import Path
from datetime import datetime,UTC
os.umask(0o077)
repo='/home/ubuntu/doxagent';sha='1e940a9a4'
subprocess.run(['sudo','-u','ubuntu','git','-C',repo,'fetch','origin'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
sha=subprocess.check_output(['git','-C',repo,'rev-parse',sha],text=True).strip()
d=Path('/var/lib/doxagent/releases/investor-access-latency-20261113');d.mkdir(exist_ok=True)
s=d/'storage_release.py';s.write_bytes(subprocess.check_output(['git','-C',repo,'show',sha+':scripts/ops/storage_release.py']))
r={'commit':sha,'started_at':datetime.now(UTC).isoformat(),'components':['v2-site-access']}
with (d/'release.private.log').open('w') as f:
 p=subprocess.run(['python3',str(s),'release','--repo',repo,'--commit',sha,'--component','v2-site-access'],stdout=f,stderr=subprocess.STDOUT)
r.update(finished_at=datetime.now(UTC).isoformat(),exit_code=p.returncode)
(d/'receipt.json').write_text(json.dumps(r));print(json.dumps(r));raise SystemExit(p.returncode)
