import json,sqlite3,time,os,subprocess
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
os.nice(10)
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');site=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data')
paths=[b/x for x in ['runtime/runtime.sqlite3','research/research.sqlite3','bus/bus.sqlite3','bus/acquisition.sqlite3','initialization/control.sqlite3','workspaces/worker-jobs.sqlite3','codex-home/logs_2.sqlite','codex-home/thread_history_1.sqlite','codex-home/state_5.sqlite']]
a=json.loads((b/'read/active.json').read_bytes());r=Path(a['path']);paths.append(b/r.relative_to('/data') if r.is_absolute() else b/'read'/r)
paths += [p for p in (site/'registry').glob('*.sqlite*') if p.suffix in ['.sqlite','.sqlite3']]
paths += list((b/'ops').glob('*/control.sqlite3'))
out={'at':datetime.now(timezone.utc).isoformat(),'databases':{}}
for p in paths:
 if not p.is_file():continue
 name=str(p.relative_to(b)) if p.is_relative_to(b) else 'site/'+p.name
 info={'path':str(p),'file_bytes':p.stat().st_size,'allocated':p.stat().st_blocks*512}
 try:
  with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.3)) as d:
   d.execute('PRAGMA query_only=ON');started=time.monotonic();d.set_progress_handler(lambda:time.monotonic()-started>3,1000)
   info['page_size']=d.execute('PRAGMA page_size').fetchone()[0];info['page_count']=d.execute('PRAGMA page_count').fetchone()[0];info['free_pages']=d.execute('PRAGMA freelist_count').fetchone()[0]
   info['schema']={n:s for n,s in d.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
   info['indices']={n:s for n,s in d.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
 except sqlite3.Error as e:info['error']=str(e)
 out['databases'][name]=info
print(json.dumps(out))
