"""Remote metadata only; no environment dumps, credentials or mutations."""
import json,subprocess
CODE='''import os,json,hashlib,sqlite3
from pathlib import Path
from datetime import datetime,UTC
from doxagent.v2_read.repository import ReadStore
out={'at':datetime.now(UTC).isoformat(),'files':{},'paths':{}}
import doxagent
root=Path(doxagent.__file__).parent
for f in ['api_v2/bus_dashboard.py','v2_read/runtime.py','v2_read/serving_fields.py','persistent_runtime_v2/w3_execution.py','codex_worker/sdk_runtime.py','codex_worker/jobs.py','persistent_runtime_v2/round_receipt.py','persistent_runtime_v2/trade_output.py']:
 p=root/f
 out['files'][f]=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH'])
out['paths']['read']=str(store.path)
with store.connect() as db:
 out['read_indexes']=[tuple(r) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND name IN ('current_source','current_page','current_parent','current_day','contributions_current_window')")]
for k in ['DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH','DOXAGENT_CODEX_WORKER_SQLITE_PATH','DOXAGENT_CODEX_WORKER_ROOT']:
 if k in os.environ:out['paths'][k]=os.environ[k]
print(json.dumps(out))
'''
out={}
for role in ['v2-api','v2-web','v2-scheduler','v2-worker','v2-message-bus']:
 ids=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project=doxagent-v2','--filter','label=com.docker.compose.service='+role],text=True).split()
 if not ids:continue
 cid=ids[0];d=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]
 im=json.loads(subprocess.check_output(['docker','image','inspect',d['Image']],text=True))[0]
 out[role]={'revision':im['Config'].get('Labels',{}).get('org.opencontainers.image.revision'),'image':d['Image'],'state':d['State']['Status']}
 if role=='v2-api':
  p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=CODE,text=True,capture_output=True,timeout=20)
  if p.returncode:raise RuntimeError(p.stderr[-1500:])
  out[role]['probe']=json.loads(p.stdout)
print(json.dumps(out))
