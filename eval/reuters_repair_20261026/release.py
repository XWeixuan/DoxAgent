"""Scoped release wrapper. Registry backup is private and never exported."""
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

COMMIT='e56b8697e21692e782783efc97da803fa62a42fb'
repo=Path('/home/ubuntu/doxagent')
assert subprocess.check_output(['git','rev-parse',COMMIT],cwd=repo,text=True).strip()==COMMIT
dbpath=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
root=Path('/var/lib/doxagent/releases/backups')
root.mkdir(mode=0o700,parents=True,exist_ok=True)
backup=root/('reuters-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.sqlite3')
fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
os.close(fd)
source=sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True,timeout=10)
with sqlite3.connect(backup) as target:
    source.backup(target)
source.close()
supervisor=subprocess.check_output(['docker','inspect','-f','{{.Id}}','doxagent-v2-v2-chrome-supervisor-1'],text=True).strip()
print(json.dumps({'stage':'BACKUP_COMPLETE','backup':str(backup),'supervisor':supervisor}),flush=True)
log=Path('/tmp/reuters-release-e56b8697.log')
fd=os.open(log,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as stream:
    code=subprocess.run(['python3','scripts/ops/storage_release.py','release','--commit',COMMIT,
        '--component','v2-site-access','--component','v2-message-bus','--component','v2-content-enrichment'],
        cwd=repo,stdout=stream,stderr=subprocess.STDOUT).returncode
after=subprocess.check_output(['docker','inspect','-f','{{.Id}}','doxagent-v2-v2-chrome-supervisor-1'],text=True).strip()
receipt={'commit':COMMIT,'returncode':code,'supervisor_unchanged':supervisor==after,
    'at':datetime.now(timezone.utc).isoformat(),'log':str(log),'backup':str(backup)}
Path('/tmp/reuters-release-receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
raise SystemExit(code)
