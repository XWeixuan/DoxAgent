import sqlite3,json
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');out={}
for name,path,cp,head in [('runtime','runtime/runtime.sqlite3',4624593,4625055),('bus','bus/bus.sqlite3',11406842,11406844),('initialization','initialization/control.sqlite3',158740,158756),('research','research/research.sqlite3',61340,61456)]:
 with sqlite3.connect((b/path).as_uri()+'?mode=ro',uri=True,timeout=.3) as s:
  out[name]=s.execute('SELECT count(*) FROM v2_source_outbox WHERE seq>? AND seq<=?',(cp,head)).fetchone()[0]
print(json.dumps(out))
