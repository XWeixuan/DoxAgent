import subprocess,json,os,difflib
from pathlib import Path
repo=Path('/home/ubuntu/doxagent');audit=Path('/home/ubuntu/runtime-cache-release-20261117');audit.mkdir(mode=0o700,exist_ok=True)
def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
git('fetch','origin','main')
dirty=git('diff','--name-only').decode().splitlines()
if set(dirty)-{'changelog'}:raise RuntimeError('unreviewed remote tracked changes: '+str(dirty))
head=git('rev-parse','HEAD').decode().strip();target=git('rev-parse','origin/main').decode().strip()
git('merge-base','--is-ancestor',head,target)
base=git('show','HEAD:changelog');current=(repo/'changelog').read_bytes()
if current.startswith(base):tail=current[len(base):]
else:
 before=base.decode('utf8').splitlines();after=current.decode('utf8').splitlines()
 ops=difflib.SequenceMatcher(a=before,b=after,autojunk=False).get_opcodes()
 if any(tag not in {'equal','insert'} for tag,*_ in ops):raise RuntimeError('remote changelog has non-additive changes')
 tail=('\n'.join(line for tag,a,b,c,d in ops if tag=='insert' for line in after[c:d])+'\n').encode('utf8')
(audit/'changelog.before').write_bytes(current)
(audit/'changelog-additions.txt').write_bytes(tail)
collisions=[]
for p in git('diff','--name-only','--diff-filter=A',head,target).decode().splitlines():
 path=repo/p
 if not path.exists():continue
 dest=audit/'pre-sync-untracked'/p;dest.parent.mkdir(parents=True,exist_ok=True)
 path.rename(dest);collisions.append(p)
try:
 (repo/'changelog').write_bytes(base)
 subprocess.run(['git','-C',str(repo),'merge','--ff-only','origin/main'],check=True,stdout=subprocess.DEVNULL)
 new=(repo/'changelog').read_bytes()
 existing=set(new.decode('utf8').splitlines())
 missing=[line for line in tail.decode('utf8').splitlines() if line and line not in existing]
 if missing:(repo/'changelog').write_bytes(new+b'\n'+'\n'.join(missing).encode('utf8')+b'\n')
except BaseException:
 (repo/'changelog').write_bytes(current)
 for p in collisions:
  if not (repo/p).exists():(audit/'pre-sync-untracked'/p).rename(repo/p)
 raise
print(json.dumps({'old':head,'head':target,'changelog_tail_bytes':len(tail),'untracked_files_backed_up':len(collisions)}))
