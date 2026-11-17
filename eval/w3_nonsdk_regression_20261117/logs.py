import json,subprocess
from pathlib import Path
ids=['case_9f9acd13af064a67a42006f1a998b9ef','case_70c5fd5cbb774da7890a08f57cc148ca','case_2ea122f6420a41e39f52abf190933a30','case_66c628e8f3bb400c97857613a6c4b39f','case_7254c65d048c42efaacddd9a6bb38cdc']
out=dict(traces=[],warnings=[])
p=subprocess.Popen(['docker','logs','--since','2026-11-17T07:00:00Z','--until','2026-11-17T08:07:00Z','doxagent-v2-v2-scheduler-1'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
for line in p.stdout:
 if 'w3_boundary_timing {' in line and any(x in line for x in ids):
  try:out['traces'].append(json.loads(line.split('w3_boundary_timing ',1)[1]))
  except ValueError:pass
 elif ('WARNING' in line or 'ERROR' in line) and 'w3_housekeeping' not in line:
  if len(out['warnings'])<150:out['warnings'].append(line[:700].strip())
p.wait();print(json.dumps(out))
