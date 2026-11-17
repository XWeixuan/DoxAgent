from pathlib import Path
import subprocess
b=Path(__file__).resolve().parent
with (b/'resources-90647e2.jsonl').open('wb',buffering=0) as out, (b/'resources-90647e2.stderr').open('wb',buffering=0) as err:
 r=subprocess.run(['ssh','-C','-o','BatchMode=yes','doxagent-sg','sudo -n python3 -'],input=(b/'resource_rss.py').read_bytes(),stdout=out,stderr=err,timeout=1900)
print(r.returncode)
