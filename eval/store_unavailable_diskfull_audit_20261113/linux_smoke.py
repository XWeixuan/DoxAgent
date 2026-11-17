import subprocess
code='''import sqlite3,tempfile,sys
from pathlib import Path
from doxagent.v2_read import cli
from doxagent.v2_read.recovery import retry_delay,retryable_storage
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.projector import ProjectionWorker
from types import SimpleNamespace
with tempfile.TemporaryDirectory(prefix='projection-recovery-smoke-') as d:
 store=ReadStore(Path(d)/'r.db');store.migrate()
 def fail(e):
  exc=sqlite3.OperationalError('fixture full');exc.sqlite_errorcode=sqlite3.SQLITE_FULL;raise exc
 worker=ProjectionWorker(store,[],fail)
 try:worker._events(SimpleNamespace(source='runtime'),[{'seq':1,'recorded_at':None}],None,budget=1)
 except sqlite3.OperationalError as exc:assert retryable_storage(exc)
 else:raise AssertionError('FULL swallowed')
 assert store.position('runtime')==0
 with store.connect() as db:assert db.execute('SELECT count(*) FROM gaps').fetchone()[0]==0
 calls=[];delays=[]
 def tick(self,**kwargs):
  calls.append(1)
  if len(calls)<3:return fail(None)
  raise SystemExit(0)
 cli.ProjectionWorker.tick=tick;cli.time.sleep=delays.append
 sys.argv=['projector','project','--read-db',str(Path(d)/'cli.db')]
 try:cli.main()
 except SystemExit:pass
 assert len(calls)==3 and delays==[1,2]
print('deployed Linux image: FULL retained coordinate and same-process retry passed')
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-projector-1','python','-'],input=code,capture_output=True,text=True,timeout=20);print(p.returncode,p.stdout,p.stderr)
