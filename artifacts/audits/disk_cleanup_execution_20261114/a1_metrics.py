import os,json,time,subprocess,stat
from pathlib import Path
from datetime import datetime,timezone
b=Path('/home/doxagent-desktop/.config/doxagent/reuters-chrome/BrowserMetrics');assert b.resolve()==b and b.is_dir()
def df():return subprocess.run(['df','-B1','/'],capture_output=True,text=True).stdout
out={'at':datetime.now(timezone.utc).isoformat(),'before':df(),'deleted':[],'skipped':[]};used=set()
for d in Path('/proc').iterdir():
 if not d.name.isdigit():continue
 try:
  for f in (d/'fd').iterdir():
   try:
    t=os.readlink(f)
    if str(b) in t:used.add(t)
   except OSError:pass
  for line in (d/'maps').read_text(errors='replace').splitlines():
   if str(b) in line:used.add(line.split(None,5)[-1])
 except OSError:pass
for p in b.glob('*.pma'):
 try:
  s=p.lstat()
  if not stat.S_ISREG(s.st_mode) or str(p) in used or time.time()-s.st_mtime<48*3600:out['skipped'].append(p.name);continue
  assert p.parent==b
  p.unlink();out['deleted'].append({'name':p.name,'allocated':s.st_blocks*512})
 except FileNotFoundError:pass
out['allocated_removed']=sum(x['allocated'] for x in out['deleted']);out['after']=df();out['containers']=subprocess.run(['docker','ps','--format','{{.Names}}|{{.Status}}'],capture_output=True,text=True).stdout;print(json.dumps(out))
