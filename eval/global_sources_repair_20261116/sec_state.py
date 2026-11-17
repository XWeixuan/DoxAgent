import json,sqlite3,os
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/sec-http/rate.json')
r={'rate_exists':p.exists()}
if p.exists():
 r.update(size=p.stat().st_size,mtime=p.stat().st_mtime)
 try:
  d=json.loads(p.read_text());r['rate_state']={k:v for k,v in d.items() if k in ['discovery','body','cooldown']}
 except Exception as e:r['parse_error']=type(e).__name__
c=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/bus.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
print(json.dumps(r))
