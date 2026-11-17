import json,subprocess
code='''import json
from importlib.metadata import version
from openai_codex.generated.v2_all import Turn,RawResponseCompletedNotification
print(json.dumps({'version':version('openai-codex'),'turn_fields':{k:Turn.model_fields[k].description for k in ['started_at','completed_at','duration_ms']},'raw_response_completed_fields':list(RawResponseCompletedNotification.model_fields)}))
'''
cid=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.service=codex-worker'],text=True).strip()
p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=code,text=True,capture_output=True,timeout=10)
if p.returncode:raise RuntimeError(p.stderr[-1000:])
print(p.stdout)
