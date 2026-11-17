import os,subprocess,json
from pathlib import Path
from datetime import datetime,UTC
os.umask(0o077);repo='/home/ubuntu/doxagent';sha='f749165b57cbf7f78d6f3837b60511c645238f4c'
subprocess.run(['sudo','-u','ubuntu','git','-C',repo,'fetch','origin'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
d=Path('/var/lib/doxagent/releases/global-sec-20261116');d.mkdir(exist_ok=True)
s=d/'storage_release.py';s.write_bytes(subprocess.check_output(['git','-C',repo,'show',sha+':scripts/ops/storage_release.py']))
roles=['v2-message-collector','v2-content-enrichment']
r={'commit':sha,'started_at':datetime.now(UTC).isoformat(),'components':roles}
args=['python3',str(s),'release','--repo',repo,'--commit',sha]
for role in roles:args+=['--component',role]
with (d/'release.private.log').open('w') as f:p=subprocess.run(args,stdout=f,stderr=subprocess.STDOUT)
r.update(finished_at=datetime.now(UTC).isoformat(),exit_code=p.returncode)
(d/'receipt.json').write_text(json.dumps(r));print(json.dumps(r));raise SystemExit(p.returncode)
