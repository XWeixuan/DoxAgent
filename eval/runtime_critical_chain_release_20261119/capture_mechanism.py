"""Disposable synthetic work-count proof, not a production latency benchmark."""
import json,sqlite3,tempfile
from pathlib import Path
from doxagent.v2_read.outbox import SourceOutbox

out=[]
with tempfile.TemporaryDirectory() as tmp:
    for capture in (True,False):
        path=Path(tmp)/('capture' if capture else 'owner-only')/'r.db';path.parent.mkdir()
        db=sqlite3.connect(path)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE runtime_values(namespace TEXT,key TEXT,payload TEXT,PRIMARY KEY(namespace,key))')
        db.commit()
        source=SourceOutbox(path,'runtime');source.migrate(('runtime_values',))
        if not capture:
            for op in ('INSERT','UPDATE','DELETE'):
                db.execute('DROP TRIGGER v2_capture_runtime_values_'+op)
            db.commit()
        before=db.total_changes
        for n in range(30):
            with db:
                db.execute('INSERT INTO runtime_values VALUES(?,?,?)',('worker_requests',str(n),json.dumps({'model':'synthetic','prompt':'x'*4096})))
        counts={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('runtime_values','v2_source_receipts','v2_source_outbox')}
        result=dict(capture=capture,total_changes=db.total_changes-before,counts=counts)
        if capture:
            result['serving_skipped']=source.event(1,serving=True).get('serving_skipped')
        out.append(result);db.close()
print(json.dumps(out))
