import json
import os
import urllib.request
from pathlib import Path
from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
result = {}
for route in ('/v1/resources', '/v1/w3/readiness'):
    request = urllib.request.Request('http://127.0.0.1:8791'+route,
        headers={'Authorization': 'Bearer '+settings.codex_worker_bearer_token})
    try:
        result[route] = json.load(urllib.request.urlopen(request, timeout=10))
    except Exception as exc:
        result[route] = dict(error=type(exc).__name__, message=str(exc))
result['processes'] = []
for directory in Path('/proc').iterdir():
    if not directory.name.isdecimal():
        continue
    try:
        command = (directory/'cmdline').read_bytes()
        if b'capsule_child' not in command and b'app-server' not in command:
            continue
        stat = (directory/'stat').read_text().rsplit(')', 1)[1].split()
        result['processes'].append(dict(pid=int(directory.name),ppid=stat[1],state=stat[0],
            role='capsule' if b'capsule_child' in command else 'app-server',
            cpu_ticks=int(stat[11])+int(stat[12]),start_ticks=stat[19]))
    except (OSError, IndexError):
        pass
print(json.dumps(result))
