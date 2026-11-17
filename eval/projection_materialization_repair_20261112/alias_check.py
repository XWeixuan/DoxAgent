import subprocess,json
out={}
code="import os,json,sqlite3;from doxagent.v2_read.repository import ReadStore; p=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']).path;d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True); print(json.dumps({'configured':os.environ['DOXAGENT_V2_READ_SQLITE_PATH'],'resolved':str(p),'generation':d.execute('SELECT id FROM generation').fetchone()[0],'checkpoint':dict(d.execute('SELECT source,position FROM checkpoints'))}))"
for name in ('v2-projector','v2-api'):
 p=subprocess.run(['docker','exec','doxagent-v2-'+name+'-1','python','-c',code],capture_output=True,text=True,timeout=20);out[name]={'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
print(json.dumps(out))
