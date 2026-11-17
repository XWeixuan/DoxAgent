from pathlib import Path
import subprocess
b=Path(__file__).resolve().parent
with (b/'samples-d33ef41.jsonl').open('wb',buffering=0) as out, (b/'samples-d33ef41.stderr').open('wb',buffering=0) as err:
 r=subprocess.run(['ssh','-C','-o','BatchMode=yes','doxagent-sg','sudo -n python3 -'],input=(b/'sample_host.py').read_bytes(),stdout=out,stderr=err,timeout=1900)
print(r.returncode)
