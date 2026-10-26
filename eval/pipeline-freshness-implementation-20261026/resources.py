"""Host read-only cumulative CPU counters; compare snapshots, no sampling daemon."""
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

out = {'at':datetime.now(UTC).isoformat(),'cpu':Path('/proc/stat').read_text().splitlines()[0],
       'pressure':Path('/proc/pressure/cpu').read_text(),
       'memory':{line.split(':')[0]:line.split(':')[1].strip() for line in Path('/proc/meminfo').read_text().splitlines() if line.split(':')[0] in ('MemAvailable','SwapFree')},'containers':{}}
ids = subprocess.check_output(['docker','ps','-q']).decode().split()
for c in json.loads(subprocess.check_output(['docker','inspect',*ids])):
    role = c['Config'].get('Labels',{}).get('com.docker.compose.service',c['Name'])
    pid = c['State']['Pid']
    group = Path(f'/proc/{pid}/cgroup').read_text().strip().split('::')[-1]
    root = Path('/sys/fs/cgroup')/group.lstrip('/')
    out['containers'][role] = {'id':c['Id'],'started_at':c['State']['StartedAt'],'restarts':c['RestartCount'],
        'cpu':(root/'cpu.stat').read_text(),'memory':int((root/'memory.current').read_text()),
        'commit':c['Config'].get('Labels',{}).get('org.opencontainers.image.revision')}
print(json.dumps(out))
