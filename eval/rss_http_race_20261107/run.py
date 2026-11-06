import base64
import json
import subprocess
import sys
import tarfile
from pathlib import Path

root=Path(__file__).parent
jobs=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
encoded=base64.b64encode(json.dumps(jobs).encode()).decode()
result=subprocess.run(['ssh','doxagent-sg','sudo -n docker exec -i doxagent-v2-v2-message-collector-1 /app/.venv/bin/python - '+encoded],
                      input=(root/'probe.py').read_bytes(),capture_output=True)
print(result.stdout.decode(),end='')
if result.stderr:print(result.stderr.decode()[-1500:],file=sys.stderr)
result.check_returncode()
names=[job['name']+ext for job in jobs for ext in ('.body','.json')]
command=('sudo -n docker exec doxagent-v2-v2-message-collector-1 tar -czf /tmp/rss-http-race-batch.tgz -C /data/diagnostics/rss-http-race-20261107 '+
         ' '.join(names)+' && sudo -n docker cp doxagent-v2-v2-message-collector-1:/tmp/rss-http-race-batch.tgz /tmp/rss-http-race-batch.tgz && sudo -n chmod 644 /tmp/rss-http-race-batch.tgz')
subprocess.run(['ssh','doxagent-sg',command],check=True,capture_output=True)
subprocess.run(['scp','doxagent-sg:/tmp/rss-http-race-batch.tgz',str(root/'batch.tgz')],check=True,capture_output=True)
with tarfile.open(root/'batch.tgz') as t:t.extractall(root,filter='data')
