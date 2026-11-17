import subprocess,json
out=[]
for role in ('v2-scheduler','codex-worker'):
 p=subprocess.Popen(['docker','logs','--since','2026-11-17T11:26:00Z','--until','2026-11-17T11:29:00Z','doxagent-v2-'+role+'-1'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 for line in p.stdout:
  if 'runtime_transaction ' in line:
   try:
    fields=dict(x.split('=',1) for x in line.split('runtime_transaction ',1)[1].split() if '=' in x)
    if int(fields.get('elapsed_ms','0'))>=800:out.append(dict(role=role,line=line.strip()[:700],elapsed_ms=int(fields['elapsed_ms'])))
   except ValueError:pass
 p.wait()
print(json.dumps(sorted(out,key=lambda x:x['elapsed_ms'],reverse=True)[:30]))
