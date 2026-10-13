"""Hash deployed source and whitelist health/config facts without exposing secrets."""
import datetime
import json
import subprocess

paths=['src/doxagent/persistent_runtime_v2/service.py','src/doxagent/persistent_runtime_v2/w3.py',
    'src/doxagent/persistent_runtime_v2/factory.py','src/doxagent/persistent_runtime_v2/maintenance.py',
    'src/doxagent/persistent_runtime_v2/policy_consumption.py','src/doxagent/persistent_runtime_v2/policy_watch.py',
    'src/doxagent/codex_worker/sdk_runtime.py','src/doxagent/codex_runtime/schema.py',
    'src/doxagent/ticker_initialization/repository.py','src/doxagent/api_v2/public_types.py',
    'src/doxagent/v2_read/artifacts.py','src/doxagent/v2_read/formal.py',
    'src/doxagent/message_bus_v2/distribution.py','src/doxagent/site_strategy/service.py',
    'prompts/persistent_runtime_v2/w2_r1.md','prompts/persistent_runtime_v2/w3/agent.md']
code='''import pathlib,json,hashlib,importlib.metadata,urllib.request,os
paths=PATHS
out={'files':{}}
for name in paths:
 p=pathlib.Path('/app')/name
 out['files'][name]=hashlib.sha256(p.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest() if p.is_file() else None
out['credentials_present']={key:bool(os.getenv(key)) for key in ('DOXAGENT_CODEX_WORKER_BEARER_TOKEN','DOXAGENT_CODEX_CAPABILITY_SECRET','DOXAGENT_CODEX_PUBLISHED_STORAGE_URL','DOXAGENT_CODEX_PUBLISHED_STORAGE_SECRET_KEY')}
out['sdk']={}
for name in ('openai-codex','openai-codex-cli-bin','ibapi'):
 try:out['sdk'][name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError:out['sdk'][name]=None
print(json.dumps(out))
'''.replace('PATHS',repr(paths))
out={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'images':{}}
for name in ('doxagent-v2-v2-api-1','doxagent-v2-v2-scheduler-1','doxagent-v2-codex-worker-1','doxagent-v2-v2-initialization-1','doxagent-v2-v2-projector-1','doxagent-v2-v2-message-bus-1','doxagent-v2-v2-site-access-1'):
 result=subprocess.run(['docker','exec',name,'python','-c',code],capture_output=True,text=True,timeout=25)
 out['images'][name]=json.loads(result.stdout) if result.returncode==0 else {'failed':result.returncode,'stderr':result.stderr[:250]}
print(json.dumps(out))
