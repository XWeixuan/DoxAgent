import subprocess,json,sys
from datetime import datetime,UTC
out=dict(at=datetime.now(UTC).isoformat(),traces=[],handoffs=[],warnings=[],error_lines=0)
for role in ('codex-worker','v2-scheduler'):
 p=subprocess.Popen(['docker','logs','--since',sys.argv[1],'doxagent-v2-'+role+'-1'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 for line in p.stdout:
  if 'w3_boundary_timing {' in line:
   try:out['traces'].append(json.loads(line.split('w3_boundary_timing ',1)[1])|dict(role=role))
   except ValueError:pass
  elif 'w3_sse_terminal {' in line:
   try:out['handoffs'].append(json.loads(line.split('w3_sse_terminal ',1)[1]))
   except ValueError:pass
  elif 'ERROR' in line or 'Traceback' in line:
   out['error_lines']+=1
   if len(out['warnings'])<30:out['warnings'].append(line[:500].strip())
 p.wait()
print(json.dumps(out))
