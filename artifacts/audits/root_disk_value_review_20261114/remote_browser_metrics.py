import os,json,time,subprocess
from pathlib import Path
b=Path('/home/doxagent-desktop/.config/doxagent/reuters-chrome/BrowserMetrics');files=[];openfiles=set();mapped=set()
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  for fd in (p/'fd').iterdir():
   try:
    target=os.readlink(fd)
    if str(b) in target:openfiles.add(target)
   except OSError:pass
  for line in (p/'maps').read_text(errors='replace').splitlines():
   if str(b) in line:mapped.add(line.split(None,5)[-1])
 except OSError:pass
for p in b.rglob('*'):
 try:
  if p.is_file():
   st=p.stat();files.append({'path':str(p),'bytes':st.st_size,'allocated':st.st_blocks*512,'mtime':st.st_mtime,'open':str(p) in openfiles,'mapped':str(p) in mapped})
 except OSError:pass
print(json.dumps({'at_epoch':time.time(),'count':len(files),'allocated':sum(x['allocated'] for x in files),'open_allocated':sum(x['allocated'] for x in files if x['open'] or x['mapped']),'closed_allocated':sum(x['allocated'] for x in files if not x['open'] and not x['mapped']),'largest':sorted(files,key=lambda x:-x['allocated'])[:20],'open_paths':list(openfiles),'mapped_paths':list(mapped)}))
