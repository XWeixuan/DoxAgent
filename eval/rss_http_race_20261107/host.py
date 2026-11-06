import subprocess,sys
from pathlib import Path
r=subprocess.run(['ssh','doxagent-sg','sudo -n python3 -'],input=Path(sys.argv[1]).read_bytes(),capture_output=True)
print(r.stdout.decode('utf8',errors='replace')[-2000:]);print(r.stderr.decode('utf8',errors='replace')[-1000:]);r.check_returncode()
if len(sys.argv)>2:Path(sys.argv[2]).write_bytes(r.stdout)
