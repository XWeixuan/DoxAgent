"""Pass a process-environment credential through SSH stdin, never arguments or files."""
import os
import subprocess
import sys
from pathlib import Path

script=sys.argv[1] if len(sys.argv)>1 else '/tmp/google-phase2-validate.py'
label=sys.argv[2] if len(sys.argv)>2 else 'validation'
result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','doxagent-sg',
    'sudo -n docker exec -i doxagent-v2-v2-site-access-1 python '+script],
    input=os.environ['ZYTE_API_KEY'],text=True,encoding='utf-8',errors='replace',stdout=subprocess.PIPE,stderr=subprocess.PIPE)
D=Path(__file__).parent
(D/(label+'-console.txt')).write_text(result.stdout,encoding='utf-8')
(D/(label+'-stderr.txt')).write_text(result.stderr,encoding='utf-8')
print('remote validation exit',result.returncode)
raise SystemExit(result.returncode)
