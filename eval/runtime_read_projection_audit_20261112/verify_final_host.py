import json,subprocess,hashlib,sqlite3
from pathlib import Path
from datetime import datetime,timezone
out={'at':datetime.now(timezone.utc).isoformat()}
info=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-projector-1']))[0]
out['image']=info['Image'];out['started']=info['State']['StartedAt'];out['running']=info['State']['Running'];out['restart_count']=info['RestartCount']
code="import json,hashlib; from pathlib import Path; root=Path('/app/src/doxagent/v2_read'); print(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in ['projector.py','cli.py','outbox.py','native_content.py','executions.py','source_lifecycle.py']}))"
out['hashes']=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-v2-projector-1','python','-c',code]))
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
read=root/'read/serving-v4-20261018/v2.sqlite3'
r=sqlite3.connect(read.as_uri()+'?mode=ro',uri=True);s=sqlite3.connect((root/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True)
out['checkpoints']=dict(r.execute('SELECT source,position FROM checkpoints'));out['runtime_head']=s.execute("SELECT head FROM v2_source_state WHERE source='runtime'").fetchone()[0]
consumer='read:'+hashlib.sha256('/data/read/serving-v4-20261018/v2.sqlite3'.encode()).hexdigest()[:24]
out['runtime_consumer_ack']=s.execute('SELECT checkpoint,updated_at FROM v2_source_consumers WHERE id=?',(consumer,)).fetchone()
out['gaps']=r.execute('SELECT source,reason,count(*) FROM gaps GROUP BY source,reason').fetchall()
print(json.dumps(out))
