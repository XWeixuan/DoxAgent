import os,json,time,hashlib,sqlite3,re
from pathlib import Path
from collections import Counter
from contextlib import closing
os.nice(10);b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');w=b/'workspaces'
summary=Counter();rows=[];errors=[]
for d in w.iterdir():
 if not d.name.startswith('runtime-maintain-') or not d.is_dir() or d.is_symlink():continue
 row={'run':d.name};summary['run_directories']+=1
 p=d/'artifacts/document3/v21_state.json'
 if p.is_file():
  st=p.stat();row['mirror_bytes']=st.st_size;row['mirror_allocated']=st.st_blocks*512;row['mirror_links']=st.st_nlink
  summary['mirror_files']+=1;summary['mirror_allocated']+=st.st_blocks*512
  if st.st_nlink==1:summary['mirror_single_link_allocated']+=st.st_blocks*512
 marker=d/'audit/retained-assets.json'
 if marker.is_file():
  try:
   m=json.loads(marker.read_bytes());row['retired']=bool(m.get('complete'));row['retired_at']=m.get('at')
   if row['retired']:
    summary['retired_runs']+=1;summary['retired_mirror_allocated']+=row.get('mirror_allocated',0)
  except Exception as e:errors.append([str(marker),str(e)[:80]])
 pairs=[]
 for manifest in (d/'published').glob('*/manifest.json'):
  try:
   data=json.loads(manifest.read_bytes())
   for item in data.get('artifacts',[]):
    rel=item['relative_path'];a=(d/rel);p=manifest.parent/rel.removeprefix('artifacts/')
    if not a.is_file() or not p.is_file():continue
    sa,sp=a.stat(),p.stat()
    if sa.st_size!=item['size_bytes'] or sp.st_size!=item['size_bytes']:continue
    if (sa.st_dev,sa.st_ino)==(sp.st_dev,sp.st_ino):summary['already_hardlinked_pairs']+=1;continue
    pairs.append({'artifact':rel,'published':str(p.relative_to(d)),'sha256':item['sha256'],'allocated':sp.st_blocks*512,'single_link':sp.st_nlink==1})
  except (OSError,ValueError,KeyError) as e:errors.append([str(manifest),str(e)[:80]])
 if pairs:
  row['duplicate_alias_candidates']=pairs;summary['manifest_matched_alias_pairs']+=len(pairs);summary['alias_allocated_candidates']+=sum(x['allocated'] for x in pairs)
  if row.get('retired'):summary['retired_alias_allocated_candidates']+=sum(x['allocated'] for x in pairs)
 rows.append(row)
samples=[]
for name in ['runtime-maintain-bbfcd27f0c1af6cb3924b4a4-o3','runtime-maintain-2efd92f6cb9175602fb806e5-o3','runtime-maintain-8a8dc6c0f5cfcaa78b610923-o3']:
 row=next((r for r in rows if r['run']==name),None)
 if not row:continue
 s={'run':name,'pairs':[]}
 for pair in row.get('duplicate_alias_candidates',[])[:5]:
  a=w/name/pair['artifact'];p=w/name/pair['published']
  with a.open('rb') as f:ha=hashlib.file_digest(f,'sha256').hexdigest()
  with p.open('rb') as f:hp=hashlib.file_digest(f,'sha256').hexdigest()
  s['pairs'].append({'artifact':pair['artifact'],'matches_manifest':ha==hp==pair['sha256'],'allocated':pair['allocated']})
 root=re.match(r'^(runtime-maintain-[0-9a-f]{24})',name)[1]
 with closing(sqlite3.connect((b/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
  s['jobs']=db.execute("SELECT status,json_extract(request,'$.node'),coalesce(json_extract(payload,'$.finished_at'),json_extract(payload,'$.updated_at')) FROM jobs WHERE json_extract(payload,'$.run_id')=? OR (json_extract(payload,'$.run_id')>=? AND json_extract(payload,'$.run_id')<?)",(root,root+'-',root+'.')).fetchall()
 with closing(sqlite3.connect((b/'research/research.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
  s['d3_state']=db.execute("SELECT json_extract(payload,'$.status'),json_extract(payload,'$.settlement.at'),json_extract(payload,'$.resource_retirement.at') FROM codex_document3_v21_runs WHERE run_id=?",(name,)).fetchall()
 samples.append(s)
print(json.dumps({'summary':dict(summary),'rows':rows,'samples':samples,'errors':errors[:30]}))
