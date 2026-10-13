"""Copy root-readable backup through SSH without changing production permissions."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

root=Path('D:/DoxAgentPilot/backups/v21-production-20261012')
root.mkdir(parents=True,exist_ok=True)
remote='/var/lib/docker/volumes/doxagent-v2_v2-data/_data/production-backups/v21-20261012/'
results=[]
for name in sys.argv[1:]:
    assert '/' not in name and '\\' not in name
    target=root/name
    with target.open('wb') as stream:
        result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','doxagent-sg','sudo','-n','cat',remote+name],stdout=stream)
    if result.returncode:
        raise SystemExit(result.returncode)
    digest=hashlib.sha256()
    with target.open('rb') as stream:
        while chunk:=stream.read(4*1024*1024):
            digest.update(chunk)
    item=dict(file=name,size=target.stat().st_size,sha256=digest.hexdigest())
    results.append(item)
    print(json.dumps(item),flush=True)
(root/('transfer-receipt-'+sys.argv[1]+'.json')).write_text(json.dumps(results,indent=2))
