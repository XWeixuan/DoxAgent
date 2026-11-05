import subprocess,json,os,sys
from pathlib import Path
mode=sys.argv[1] if len(sys.argv)>1 else 'PAPER';assert mode=='PAPER'
name='doxagent-v2-v2-executor-1';root=Path('/var/lib/doxagent/releases/be-repair-515d66ef/acceptance');info=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
assert info['Image'].startswith('sha256:71be04315849') and info['State']['Running']
probe="import sqlite3,os;d=sqlite3.connect('file:'+os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3')+'?mode=ro',uri=True);assert d.execute(\"select count(*) from te_jobs where state not in ('DONE','FAILED')\").fetchone()[0]==0;assert all(not __import__('json').loads(r[0])['remaining_qty'] for r in d.execute('select payload from te_lots'))"
subprocess.run(['docker','exec',name,'python','-c',probe],check=True)
envfile=root/'helper.env'
with envfile.open('w') as f:
 os.fchmod(f.fileno(),0o600)
 for value in info['Config']['Env']:
  assert '\n' not in value;f.write(value+'\n')
network=next(iter(info['NetworkSettings']['Networks']))
try:
 subprocess.run(['docker','stop','--time','30',name],stdout=subprocess.DEVNULL,check=True)
 args=['docker','run','--rm','--name','doxagent-onds1-'+mode.lower()+'-r2',
 '--network',network,'--add-host','host.docker.internal:host-gateway','--volumes-from',name,
 '--env-file',str(envfile),'-v',str(root)+':/acceptance','--entrypoint','python',info['Image'],'/acceptance/paper-r2.py',mode]
 r=subprocess.run(args,text=True,capture_output=True,timeout=120)
 (root/(mode.lower()+'-r2.stdout')).write_text(r.stdout);(root/(mode.lower()+'-r2.stderr')).write_text(r.stderr)
 print(r.stdout,flush=True)
 if r.returncode:print('ACCEPTANCE_ERROR',r.stderr[-2000:],flush=True)
 assert r.returncode==0
finally:
 subprocess.run(['docker','start',name],stdout=subprocess.DEVNULL,check=True);envfile.unlink(missing_ok=True)
