import subprocess,json,re
out=[];current=None
p=subprocess.Popen(['docker','logs','--since','2026-11-17T07:10:00Z','--until','2026-11-17T07:20:00Z','doxagent-v2-v2-scheduler-1'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
for line in p.stdout:
 if 'Exception in thread' in line:
  if current:out.append(current)
  current=[line.strip()]
 elif current is not None:
  if re.match(r'^2026-\d\d-\d\d ',line) or len(current)>110:
   out.append(current);current=None
  elif line.strip():current.append(line[:600].strip())
 if len(out)>=4:break
p.stdout.close();p.terminate();p.wait();print(json.dumps(out))
