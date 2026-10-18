from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
from pathlib import Path
import sqlite3

from doxagent.event_library.repository import EventLibraryRepository
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.resource_gc import collect
from doxagent.persistent_runtime_v2.retained_events import snapshot_digest
from tests.test_codex_event_library_incremental import _publish_v1


def test_owner_retires_unreferenced_compacts_history_and_keeps_current_and_frame(tmp_path):
    now=datetime.now(UTC);old=now-timedelta(days=12)
    journal=RuntimeJournal(tmp_path/'runtime'/'runtime.sqlite3',clock=lambda:old)
    roots={}
    for name in ('old','current','unused','inflight'):
        root=journal.path.parent/'runtime_artifacts'/('runtime-maintain-'+name)/'events';roots[name]=root
        repository=EventLibraryRepository(root/'US/MU/event_library.sqlite3');_publish_v1(repository,tmp_path/('fixture-'+name))
        with repository._write() as db:
            db.execute('CREATE TABLE v2_source_receipts(id PRIMARY KEY,payload)');db.execute('INSERT INTO v2_source_receipts VALUES(1,?)',('x'*1000000,))
        identity='task-'+name;journal.put_task(identity,'MU','MAINTENANCE',{})
        frame={'run_id':'runtime-maintain-'+name,'base':{'artifacts':{'event_library':{'root':str(roots[name] if name=='inflight' else roots.get('current',root)),'version':1}},'ticker':'MU'}}
        with journal.transaction() as db:
            db.execute('UPDATE runtime_tasks SET status=?,receipt=? WHERE id=?',('RUNNING' if name=='inflight' else 'SUCCEEDED',json.dumps({'frame':frame}),identity))
    control=tmp_path/'control.db'
    with closing(sqlite3.connect(control)) as db,db:
        db.execute('CREATE TABLE activation_revisions(revision_id TEXT PRIMARY KEY,ticker TEXT,payload TEXT)')
        db.execute('CREATE TABLE ticker_active_revision(ticker TEXT,revision_id TEXT)')
        for name in ('old','current'):
            payload={'ticker':'MU','artifacts':{'event_library':{'root':str(roots[name]),'version':1}}}
            db.execute('INSERT INTO activation_revisions VALUES(?,?,?)',(name,'MU',json.dumps(payload)))
        db.execute("INSERT INTO ticker_active_revision VALUES('MU','current')")
    key='runtime-worker:runtime-maintain-old:runtime-maintain-old-o3:d3_o3_maintain:maintain'
    journal.set('worker_requests',key,dict(run_id='runtime-maintain-old-o3',attempt_id='a',node='d3_o3_maintain',prompt='p'*50000,task_phase=None))
    journal.set('worker_receipts',key,dict(status='succeeded',final_response='unique final',telemetry={'usage':{'total_tokens':1},'events':['technical log']}))
    journal.set('reference_baselines','task-old',dict(version=1,events=['duplicate retry snapshot']))
    journal.set('reference_deliveries','delivered',dict(root=str(roots['old']),ticker='MU',status='SUCCEEDED',before=dict(version=1,events=['delivered evidence']),after=dict(version=1)))
    original=EventLibraryRepository(roots['old']/'US/MU/event_library.sqlite3',read_only=True);before=snapshot_digest(original,'MU',1)
    report=collect(journal,control,now=now,limit=10)
    assert not roots['unused'].parent.exists()
    assert roots['current'].is_dir() and roots['inflight'].is_dir()
    target=EventLibraryRepository(roots['old']/'US/MU/event_library.sqlite3',read_only=True)
    assert snapshot_digest(target,'MU',1)==before
    assert len(report['compacted'])==1 and len(report['retired'])==1,report
    assert journal.get('worker_requests',key)['retired_process']
    final=journal.get('worker_receipts',key)
    assert final['final_response']=='unique final' and final['telemetry']['usage']=={'total_tokens':1}
    assert journal.get('reference_baselines','task-old') is None
    assert journal.get('reference_deliveries','delivered')['before']['events']==['delivered evidence']
    again=collect(journal,control,now=now,limit=10)
    assert again['compacted']==[] and again['retired']==[]
