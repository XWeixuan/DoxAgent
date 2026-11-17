from pathlib import Path
import subprocess,sys
base=Path(__file__).resolve().parent
name,output=sys.argv[1:3]
r=subprocess.run(['ssh','-C','-o','BatchMode=yes','-o','ConnectTimeout=15','doxagent-sg','sudo -n python3 -'],input=(base/name).read_bytes(),capture_output=True,timeout=int(sys.argv[3]) if len(sys.argv)>3 else 60)
(base/output).write_bytes(r.stdout);(base/(output+'.stderr')).write_bytes(r.stderr)
print('exit',r.returncode,'bytes',len(r.stdout),'output',output)
if r.returncode:print(r.stderr.decode(errors='replace')[-1000:])
