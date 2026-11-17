import sqlite3,json,subprocess
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');r=sqlite3.connect((b/'read/serving-v4-20261018/v2.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=1);s=sqlite3.connect((b/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=1)
rows=r.execute("SELECT c.source_event,c.at,t.projected_at,(julianday(t.projected_at)-julianday(c.at))*86400 lag FROM commit_retention t JOIN commits c ON c.seq=t.seq WHERE t.projected_at>=? AND c.source='runtime' AND c.source_event NOT LIKE '%:repair:%' ORDER BY lag DESC LIMIT 15",('2026-11-13T16:10:00Z',)).fetchall()
out=[]
for event,at,projected,lag in rows:
 row=s.execute('SELECT table_name FROM v2_source_outbox WHERE seq=?',(int(event),)).fetchone() if event.isdigit() else None
 out.append({'event':event,'at':at,'projected':projected,'lag':lag,'table':row})
print(json.dumps(out))
print('minute counts',r.execute("SELECT substr(t.projected_at,1,16),count(*),max((julianday(t.projected_at)-julianday(c.at))*86400) FROM commit_retention t JOIN commits c ON c.seq=t.seq WHERE t.projected_at>=? AND c.source='runtime' AND c.source_event NOT LIKE '%:repair:%' GROUP BY 1",('2026-11-13T16:10:00Z',)).fetchall())
print('PSI',{k:Path('/proc/pressure/'+k).read_text() for k in ['cpu','io']})
