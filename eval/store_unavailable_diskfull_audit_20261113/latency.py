import sqlite3,json
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
r=sqlite3.connect((b/'read/serving-v4-20261018/v2.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=1)
out={}
for source in ['runtime','bus','initialization','research']:
 v=[x[0] for x in r.execute("SELECT (julianday(t.projected_at)-julianday(c.at))*86400 FROM commit_retention t JOIN commits c ON c.seq=t.seq WHERE t.projected_at>=? AND c.source=? AND c.source_event NOT LIKE '%:repair:%'",('2026-11-13T16:10:00Z',source)) if x[0] is not None];v.sort()
 out[source]={'n':len(v),'p95':v[min(len(v)-1,int(len(v)*.95))] if v else None,'p99':v[min(len(v)-1,int(len(v)*.99))] if v else None,'max':max(v) if v else None}
print(json.dumps(out))
