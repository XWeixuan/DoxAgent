import os,json,time
from pathlib import Path
from datetime import datetime
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/boundary-timing')
a=datetime.fromisoformat('2026-11-17T11:28:00+00:00').timestamp();b=a+25
out=dict(scanned=0,selected=0,truncated=False,traces=[])
if root.exists():
 with os.scandir(root) as entries:
  for entry in entries:
   out['scanned']+=1
   if out['scanned']>4096:out['truncated']=True;break
   st=entry.stat(follow_symlinks=False)
   if not a<=st.st_mtime<=b or st.st_size>131072 or not entry.is_file(follow_symlinks=False):continue
   out['selected']+=1
   if out['selected']>32:out['truncated']=True;break
   try:v=json.loads(Path(entry.path).read_text())
   except (ValueError,OSError):continue
   if v.get('run_id') in ('w3-ready-f63c3e85e2dd49508daf7394072245f4','w3-ready-1d539a23f4194617b2c019eaa4fa49ed'):
    out['traces'].append({k:v.get(k) for k in ('run_id','plane','elapsed_ms','started_at','finished_at','totals')})
print(json.dumps(out))
