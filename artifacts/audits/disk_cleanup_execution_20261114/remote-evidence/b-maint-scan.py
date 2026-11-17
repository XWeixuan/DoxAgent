import os,json,time,sqlite3,re
from pathlib import Path
from collections import Counter,defaultdict
os.nice(10);w=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces');out={'summary':{},'runs':[]};seen=set();stats=Counter();start=time.monotonic()
with sqlite3.connect((w/'worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
 active={r[0] for r in db.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE status IN ('running','queued')") if r[0]};fenced={r[0].removeprefix('workspace_retirement:') for r in db.execute("SELECT key FROM metadata WHERE key LIKE 'workspace_retirement:%'")}
for d in sorted(w.glob('runtime-maintain-*')):
 if not d.is_dir() or d.is_symlink():continue
 marker=d/'audit/retained-assets.json';retired=False
 if marker.is_file():
  try:retired=json.loads(marker.read_bytes()).get('complete',False)
  except Exception:pass
 areas=Counter();top=[];files=0
 for directory,dirs,names in os.walk(d,followlinks=False):
  for name in names:
   p=Path(directory)/name
   try:
    st=p.lstat();key=(st.st_dev,st.st_ino)
    if key in seen:continue
    seen.add(key);files+=1;rel=p.relative_to(d);area=rel.parts[0] if len(rel.parts)>1 else 'root-files';areas[area]+=st.st_blocks*512
    if st.st_blocks*512>=20*2**20:top.append({'path':str(rel),'allocated':st.st_blocks*512,'links':st.st_nlink})
   except FileNotFoundError:pass
 row={'run':d.name,'retired':retired,'fenced':d.name in fenced,'areas':dict(areas),'allocated':sum(areas.values()),'files':files,'large_files':top};out['runs'].append(row)
 for area,v in areas.items():stats[('retired:' if retired else 'unretired:')+area]+=v
 if time.monotonic()-start>240:out['partial']=True;break
out['summary']=dict(stats);out['scanned_runs']=len(out['runs']);out['elapsed']=time.monotonic()-start;out['largest_runs']=sorted(out['runs'],key=lambda x:-x['allocated'])[:25];print(json.dumps(out))
