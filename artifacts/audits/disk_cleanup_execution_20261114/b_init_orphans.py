import json,os,time,sqlite3,re,stat,subprocess
from pathlib import Path
w=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces');b=w/'.init-contents';assert b.resolve()==b
with sqlite3.connect((w/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
 active=[r[0] for r in db.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE status IN ('queued','running')") if r[0]]
assert not any(x.startswith(('init-','d2ws-','rerun-')) for x in active),'initialization adoption active'
used=set()
for proc in Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 try:
  for fd in (proc/'fd').iterdir():
   try:
    x=os.readlink(fd)
    if '.init-contents/' in x:used.add(Path(x).name)
   except OSError:pass
 except OSError:pass
out={'before':subprocess.run(['df','-B1','/'],capture_output=True,text=True).stdout,'deleted':[],'retained_linked':0,'retained_recent':0}
for p in b.iterdir():
 st=p.lstat()
 if not stat.S_ISREG(st.st_mode) or not re.fullmatch('[a-f0-9]{64}-[0-7]+-[0-9]+-[0-9]+',p.name):continue
 if st.st_nlink!=1:out['retained_linked']+=1;continue
 if time.time()-st.st_mtime<86400 or p.name in used:out['retained_recent']+=1;continue
 again=p.stat()
 if again.st_nlink!=1 or again.st_ino!=st.st_ino:continue
 p.unlink();out['deleted'].append({'name':p.name,'allocated':st.st_blocks*512})
out['allocated_removed']=sum(x['allocated'] for x in out['deleted']);out['after']=subprocess.run(['df','-B1','/'],capture_output=True,text=True).stdout;print(json.dumps(out))
