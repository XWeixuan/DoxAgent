import json,subprocess,time,os
from datetime import datetime,timezone
print(json.dumps({'phase':'start','at':datetime.now(timezone.utc).isoformat(),'df':subprocess.check_output(['df','-B1','/'],text=True),'inodes':subprocess.check_output(['df','-i','/'],text=True)}),flush=True)
args=['nice','-n','10','ionice','-c','2','-n','7','du','-x','-B1','-d','8','/']
p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
rows={};last=time.monotonic()
for line in p.stdout:
 number,sep,path=line.rstrip('\n').partition('\t')
 if sep and number.isdigit():rows[path]=int(number)
 if time.monotonic()-last>=30:
  print(json.dumps({'phase':'progress','at':datetime.now(timezone.utc).isoformat(),'directory_rows':len(rows),'last_path':path}),flush=True);last=time.monotonic()
stderr=p.stderr.read();code=p.wait()
print(json.dumps({'phase':'finished','at':datetime.now(timezone.utc).isoformat(),'exit':code,'root_total_present':'/' in rows,'df':subprocess.check_output(['df','-B1','/'],text=True),'sizes':rows,'stderr':stderr[-12000:],'root_files':[{'path':str(x),'allocated':x.stat().st_blocks*512,'size':x.stat().st_size} for x in __import__('pathlib').Path('/').iterdir() if x.is_file() and not x.is_symlink()]}),flush=True)
