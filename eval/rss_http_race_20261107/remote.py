import subprocess,sys
from pathlib import Path
p=Path(__file__).parent
r=subprocess.run(['ssh','doxagent-sg','sudo -n docker exec -i doxagent-v2-v2-message-collector-1 /app/.venv/bin/python -'],input=Path(sys.argv[1]).read_bytes(),capture_output=True)
(p/sys.argv[2]).write_bytes(r.stdout)
print('exit',r.returncode,'bytes',len(r.stdout),r.stderr.decode()[-1500:]);r.check_returncode()
