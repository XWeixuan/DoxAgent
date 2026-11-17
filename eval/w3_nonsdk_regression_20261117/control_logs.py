import subprocess,json,re,collections
out=dict(renewal=[],control=[],hot=[],slow=[],counts={})
c=collections.Counter()
p=subprocess.Popen(['docker','logs','--since','2026-11-17T07:00:00Z','--until','2026-11-17T07:26:00Z','doxagent-v2-v2-scheduler-1'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
for line in p.stdout:
 if 'w3_boundary_timing' in line:continue
 if 'renewal' in line or 'lease lost' in line.lower() or 'LeaseLost' in line:
  if len(out['renewal'])<80:out['renewal'].append(line[:700].strip())
 if 'runtime_hot_lane' in line and ('case_9f9a' in line or 'case_70c5' in line or 'case_2ea1' in line):out['hot'].append(line[:700].strip())
 if 'runtime_transaction' in line:
  m=re.search(r'operation=(\S+).*?elapsed_ms=(\d+).*?wait_ms=(\d+)',line)
  if m:
   c[m[1]]+=1
   if int(m[2])>2000:out['slow'].append(line[:700].strip())
 elif any(s in line for s in ('Traceback','ERROR','provider','resum','lease')) and len(out['control'])<60:out['control'].append(line[:600].strip())
p.wait();out['counts']=c;print(json.dumps(out))
