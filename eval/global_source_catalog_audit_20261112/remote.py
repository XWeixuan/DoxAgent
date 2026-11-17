"""Run a reviewed read-only probe over the existing SSH host alias."""
import subprocess,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
base=Path(__file__).resolve().parent
p=subprocess.run(['ssh','-C','-o','BatchMode=yes','-o','ConnectTimeout=15','doxagent-sg','sudo -n python3 -'],input=(base/sys.argv[1]).read_bytes(),capture_output=True,timeout=1000)
(base/sys.argv[2]).write_bytes(p.stdout)
(base/(sys.argv[2]+'.stderr')).write_bytes(p.stderr)
print(p.stdout.decode(errors='replace')[-7000:]);print(p.stderr.decode(errors='replace')[-1500:])
raise SystemExit(p.returncode)
