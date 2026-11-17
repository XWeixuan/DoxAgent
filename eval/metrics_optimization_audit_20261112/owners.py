import json,subprocess
code='''import os,json,hashlib
from pathlib import Path
import doxagent
root=Path(doxagent.__file__).parent
files=['persistent_runtime_v2/w3_execution.py','persistent_runtime_v2/round_receipt.py','codex_worker/sdk_runtime.py','codex_worker/jobs.py','persistent_runtime_v2/trade_output.py']
out={'files':{f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in files},'paths':{k:v for k,v in os.environ.items() if ('SQLITE_PATH' in k or k.endswith('WORKSPACE_ROOT') or k.endswith('WORKSPACES_ROOT')) and (Path(v).exists())}}
print(json.dumps(out))
'''
rows=subprocess.check_output(['docker','ps','--format','{{.ID}} {{.Label "com.docker.compose.service"}}'],text=True).splitlines()
out={'services':rows,'owners':{}}
for row in rows:
 cid,_,role=row.partition(' ')
 if role!='v2-scheduler' and 'worker' not in role:continue
 p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=code,text=True,capture_output=True,timeout=15)
 out['owners'][role]=json.loads(p.stdout) if p.returncode==0 else {'error':p.stderr[-1000:]}
print(json.dumps(out))
