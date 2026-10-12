import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding='utf-8')
def remote(command, source=None):
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no', 'doxagent-sg', command], input=source, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    return result.stdout

for container, path, name in [
    ('doxagent-v2-v2-scheduler-1', '/app/src/doxagent/persistent_runtime_v2/w3.py', 'deployed_w3.py'),
    ('doxagent-v2-codex-worker-1', '/app/src/doxagent/codex_worker/sdk_runtime.py', 'deployed_sdk_runtime.py'),
    ('doxagent-v2-v2-scheduler-1', '/app/src/doxagent/persistent_runtime_v2/service.py', 'deployed_service.py'),
]:
    (HERE/name).write_bytes(remote(f'sudo -n docker exec {container} cat {path}'))
payload = remote('sudo -n python3 -u -', (HERE/'probe_remote.py').read_bytes())
data = json.loads(payload)
(HERE/'evidence.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'summary':data['summary'], 'cases':[{**{k:c.get(k) for k in ['case_id','ticker','title','created_at','mode','thread_kind','has_intent','worker_seconds','sdk_seconds']},'compactions_in_turn':[e for e in c['compactions'] if e['type']=='compacted' and e['in_selected_turn']]} for c in data['cases']]},ensure_ascii=False,indent=2))
