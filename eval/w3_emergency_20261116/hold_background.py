"""One-release hold of unclaimed maintenance only. No Case/effect/lease edits."""
import json,sqlite3,sys
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3')
f=Path('/var/lib/doxagent/releases/w3-emergency-20261116/held-maintenance.json')
d=sqlite3.connect(p,timeout=.1);d.row_factory=sqlite3.Row
if sys.argv[1]=='hold':
 ids=json.loads(f.read_text()) if f.exists() else []
 found=[r[0] for r in d.execute("select id from runtime_tasks where kind in ('MAINTENANCE','SWEEP','SELECTION') and status='PENDING'")]
 ids=list(dict.fromkeys(ids+found));f.write_text(json.dumps(ids));f.chmod(0o600)
 with d:
  n=sum(d.execute("update runtime_tasks set status='HELD' where id=? and kind in ('MAINTENANCE','SWEEP','SELECTION') and status='PENDING'",(x,)).rowcount for x in found)
 print(json.dumps(dict(held=n,recorded=len(ids))))
else:
 ids=json.loads(f.read_text()) if f.exists() else []
 with d:
  n=sum(d.execute("update runtime_tasks set status='PENDING' where id=? and status='HELD'",(x,)).rowcount for x in ids)
 print(json.dumps(dict(restored=n)))
