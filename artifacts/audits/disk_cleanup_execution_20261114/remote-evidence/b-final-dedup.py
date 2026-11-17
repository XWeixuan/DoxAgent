import os,json,time,sqlite3,hashlib,re,subprocess,stat
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
os.nice(10);b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');w=b/'workspaces';out={'at':datetime.now(timezone.utc).isoformat(),'runs':[],'errors':[]};stats=Counter()
def df():return subprocess.run(['df','-B1','/'],capture_output=True,text=True).stdout
def ro(p):return sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=5)
def digest(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def safe(p,d):return not p.is_symlink() and p.resolve().is_relative_to(d.resolve())
out['before']=df()
with ro(w/'worker-jobs.sqlite3') as db:
 active={r[0] for r in db.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE status IN ('queued','running')") if r[0]}
 fences={r[0].removeprefix('workspace_retirement:') for r in db.execute("SELECT key FROM metadata WHERE key LIKE 'workspace_retirement:%'")}
for d in sorted(w.glob('runtime-maintain-*')):
 if not d.is_dir() or d.is_symlink():continue
 run=d.name;root=re.match(r'^(runtime-maintain-[0-9a-f]{24})(?:-|$)',run)
 if not root:continue
 root=root[1];marker=d/'audit/retained-assets.json'
 try:
  if not marker.is_file():continue
  m=json.loads(marker.read_bytes())
  if not m.get('complete') or not m.get('publication'):stats['no_publication_or_incomplete']+=1;continue
  if run not in fences:stats['unfenced']+=1;continue
  if any(a==root or a.startswith(root+'-') for a in active):stats['active']+=1;continue
  pairs=[];verified=set()
  for manifest in (d/'published').glob('*/manifest.json'):
   if not safe(manifest,d):continue
   data=json.loads(manifest.read_bytes())
   for item in data.get('artifacts',[]):
    rel=item['relative_path'];a=d/rel;p=manifest.parent/rel.removeprefix('artifacts/')
    if not (safe(a,d) and safe(p,d) and a.is_file() and p.is_file()):continue
    sa,sp=a.stat(),p.stat()
    if sa.st_size!=item['size_bytes'] or sp.st_size!=item['size_bytes']:raise ValueError('manifest size mismatch')
    if (sa.st_dev,sa.st_ino)==(sp.st_dev,sp.st_ino):verified.add(rel);continue
    if digest(a)!=item['sha256'] or digest(p)!=item['sha256']:raise ValueError('manifest hash mismatch')
    verified.add(rel);pairs.append((a,p,sa,sp,item['sha256']))
  if not verified:stats['no_verified_final']+=1;continue
  # Existing owner-retirement fences prevent resubmission; refresh live jobs immediately before mutation.
  with ro(w/'worker-jobs.sqlite3') as db:
   live=db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND (json_extract(payload,'$.run_id')=? OR (json_extract(payload,'$.run_id')>=? AND json_extract(payload,'$.run_id')<?)) LIMIT 1",(root,root+'-',root+'.')).fetchone()
  if live:stats['became_active']+=1;continue
  row={'run':run,'aliases':0,'alias_allocated_released':0,'mirror_allocated_released':0}
  for a,p,sa,sp,sha in pairs:
   if p.stat().st_ino!=sp.st_ino or a.stat().st_ino!=sa.st_ino:raise ValueError('inode changed')
   tmp=p.with_name('.'+p.name+'.dedup-20261114')
   if tmp.exists():raise ValueError('temporary path exists')
   os.link(a,tmp);os.replace(tmp,p)
   assert a.stat().st_ino==p.stat().st_ino
   row['aliases']+=1
   if sp.st_nlink==1:row['alias_allocated_released']+=sp.st_blocks*512
  mirror=d/'artifacts/document3/v21_state.json'
  if mirror.is_file() and safe(mirror,d):
   st=mirror.stat();row['mirror_sha256']=digest(mirror);row['mirror_bytes']=st.st_size
   mirror.unlink();row['mirror_allocated_released']=st.st_blocks*512 if st.st_nlink==1 else 0
  if row['aliases'] or row['mirror_allocated_released']:
   receipt=d/'audit/storage-exit-20261114.json';receipt.write_text(json.dumps(dict(row,at=out['at'],basis='existing-retirement-fence-and-verified-publication',resume_allowed=False)))
   out['runs'].append(row);stats['processed']+=1;stats['alias_allocated_released']+=row['alias_allocated_released'];stats['mirror_allocated_released']+=row['mirror_allocated_released']
   if stats['processed']%300==0:print(json.dumps({'progress':dict(stats),'df':df()}),flush=True)
 except (OSError,ValueError,KeyError,sqlite3.Error) as e:out['errors'].append({'run':run,'error':str(e)})
out['summary']=dict(stats);out['after']=df();print(json.dumps({'result':out}),flush=True)
