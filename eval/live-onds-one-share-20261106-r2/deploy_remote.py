import subprocess,json,tarfile,hashlib,importlib.util,os
from pathlib import Path
root=Path('/var/lib/doxagent/releases');directory=root/'completed-history-de78ec13';directory.mkdir(mode=0o700,exist_ok=True);context=directory/'context';context.mkdir(exist_ok=True)
with tarfile.open('/tmp/completed-history-de78ec13.tar') as t:t.extractall(context,filter='data')
name='doxagent-v2-v2-executor-1';info=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
base='doxagent-v2:completed-history-parent-de78ec13'
if subprocess.run(['docker','image','inspect',info['Image']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
 parent=subprocess.check_output(['docker','commit','--pause=false',name,base],text=True).strip()
else:
 subprocess.run(['docker','tag',info['Image'],base],check=True);parent=info['Image']
files=['src/doxagent/trade_execution/'+n+'.py' for n in ['ibkr_session']]
(context/'Dockerfile').write_text('\n'.join(['FROM '+base]+['COPY '+p+' /app/'+p for p in files]+['LABEL org.opencontainers.image.revision="de78ec13"'])+'\n')
tag='doxagent-v2:completed-history-de78ec13'
with (directory/'build.log').open('w') as log:subprocess.run(['docker','build','-t',tag,str(context)],stdout=log,stderr=subprocess.STDOUT,check=True)
image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip()
checks={p:hashlib.sha256((context/p).read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest() for p in files}
probe="import sqlite3,json,os;db=sqlite3.connect('file:'+os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3')+'?mode=ro',uri=True);assert db.execute(\"select count(*) from te_attempts where state not in ('SETTLED','NOT_SENT')\").fetchone()[0]==0"
subprocess.run(['docker','run','--rm','--volumes-from',name,'--env','DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH=/data/runtime/runtime.sqlite3','--entrypoint','python',image,'-c',probe],check=True)
spec=importlib.util.spec_from_file_location('release','/home/ubuntu/doxagent/scripts/ops/storage_release.py');release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
with release.lock(root):
 state=json.loads((root/'state.json').read_text());cfg=json.loads((root/'compose.current.json').read_text());release.atomic(directory/'compose.before.json',cfg)
 candidate={k:v for k,v in cfg['services']['v2-executor'].items() if k in ['environment','command','entrypoint']}
 checkcode="import hashlib;from pathlib import Path;expected="+repr(checks)+";assert all(hashlib.sha256(Path('/app/'+p).read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest()==h for p,h in expected.items())"
 candidate.update(image=image,checks=[['docker','exec',name,'python','-c',checkcode]])
 manifest=dict(commit='de78ec13',compose_command=state['compose_base_command'],components={'v2-executor':candidate});release.atomic(directory/'manifest.json',manifest)
 result=release.deploy(root,state,manifest);release.atomic(directory/'release-result.json',result)
release.atomic(directory/'verification.json',dict(image=image,parent=parent,source_hashes=checks,commit='de78ec13'))
print(json.dumps(dict(released=True,image=image,source_hashes=checks)),flush=True)
