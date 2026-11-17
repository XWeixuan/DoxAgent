import os,json,sqlite3,subprocess
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/read');a=json.loads((b/'active.json').read_text());out={'active':a,'holders':[]}
needle='/read/serving-v4-20261018/v2.sqlite3'
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 hits=[]
 try:
  for fd in (p/'fd').iterdir():
   try:
    s=os.readlink(fd)
    if needle in s:hits.append(s)
   except OSError:pass
  if hits:out['holders'].append({'pid':p.name,'cgroup':(p/'cgroup').read_text(),'comm':(p/'comm').read_text().strip(),'files':list(set(hits))})
 except OSError:pass
p=b/'serving-v4-20261018/v2.sqlite3'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 out['pages']={k:db.execute('PRAGMA '+k).fetchone()[0] for k in ['page_size','page_count','freelist_count','user_version']};out['tables']=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
out['containers']=subprocess.run(['docker','ps','--no-trunc','--format','{{.ID}}|{{.Names}}'],capture_output=True,text=True).stdout;print(json.dumps(out))
