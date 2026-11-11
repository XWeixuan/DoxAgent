import subprocess
CODE='''import os,json,sqlite3
from pathlib import Path
from doxagent.v2_read.repository import ReadStore
store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']);pos=store.position('runtime')
path=Path(os.environ['DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH']);db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
latest=db.execute('SELECT seq,recorded_at FROM v2_source_outbox ORDER BY seq DESC LIMIT 1').fetchone()
nextrow=db.execute('SELECT seq,recorded_at FROM v2_source_outbox WHERE seq>? ORDER BY seq LIMIT 1',(pos,)).fetchone()
print(json.dumps({'position':pos,'latest':latest,'next':nextrow}))
'''
cid=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.service=v2-projector'],text=True).strip()
p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=CODE,text=True,capture_output=True,timeout=15)
print(p.stdout)
if p.returncode:raise RuntimeError(p.stderr[-1000:])
