import os,subprocess,json,copy,importlib.util
from pathlib import Path
from datetime import datetime,UTC
os.umask(0o077)
repo='/home/ubuntu/doxagent';sha='2d2f28bff'
subprocess.run(['sudo','-u','ubuntu','git','-C',repo,'fetch','origin'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
sha=subprocess.check_output(['git','-C',repo,'rev-parse',sha],text=True).strip()
root=Path('/var/lib/doxagent/releases');d=root/'global-yahoo-20261116';d.mkdir(exist_ok=True)
script=d/'storage_release.py';script.write_bytes(subprocess.check_output(['git','-C',repo,'show',sha+':scripts/ops/storage_release.py']))
spec=importlib.util.spec_from_file_location('release',script);release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
receipt={'commit':sha,'started_at':datetime.now(UTC).isoformat(),'components':['v2-message-collector']}
with (d/'release.private.log').open('w') as log:
 oldout,olderr=os.dup(1),os.dup(2)
 try:
  os.dup2(log.fileno(),1);os.dup2(log.fileno(),2)
  with release.lock(root):
   built=release.build(Path(repo),sha,root,['backend'])
   state=json.loads((root/'state.json').read_text());current=state['components']['v2-message-collector']['current']
   candidate=copy.deepcopy(current.get('compose',{}));candidate['image']=built['images']['backend']['image'];candidate['schema']=current.get('schema')
   candidate.setdefault('environment',{}).update(DOXAGENT_BUILD_COMMIT=sha,DOXAGENT_SOURCE_SHA256=built['source_archive_sha256'],
       DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED='true',DOXAGENT_MESSAGE_BUS_V2_YAHOO_HTTP_ROUTES='["de-standard-1","tr-standard-1"]')
   manifest={'compose_command':state['compose_base_command'],'commit':sha,'components':{'v2-message-collector':candidate}}
   release.atomic(d/'manifest.private.json',manifest)
   release.deploy(root,state,manifest)
  receipt['exit_code']=0
 except Exception as e:
  receipt.update(exit_code=1,error_type=type(e).__name__)
 finally:
  os.dup2(oldout,1);os.dup2(olderr,2);os.close(oldout);os.close(olderr)
receipt['finished_at']=datetime.now(UTC).isoformat()
(d/'receipt.json').write_text(json.dumps(receipt));print(json.dumps(receipt),flush=True)
raise SystemExit(receipt['exit_code'])
