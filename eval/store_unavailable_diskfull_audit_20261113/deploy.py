import subprocess,json
from pathlib import Path
repo='/home/ubuntu/doxagent';commit='9fedb4ea9'
subprocess.run(['sudo','-u','ubuntu','git','-C',repo,'fetch','origin','codex/projection-interruption-recovery'],check=True,stdout=subprocess.DEVNULL)
raw=subprocess.check_output(['sudo','-u','ubuntu','git','-C',repo,'show',commit+':scripts/ops/projection_performance_release_20261112.py'])
for action in ['build','deploy']:
 p=subprocess.run(['python3','-',action,'--commit',commit,'--baseline-commit','741043666','--recovery'],input=raw,capture_output=True,timeout=240)
 print(action,p.returncode,p.stdout.decode()[-4500:],p.stderr.decode()[-1500:],flush=True)
 if p.returncode:raise SystemExit(p.returncode)
