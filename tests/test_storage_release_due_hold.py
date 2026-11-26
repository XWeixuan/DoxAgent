import importlib.util
from pathlib import Path
from datetime import UTC,datetime,timedelta
from doxagent.persistent_runtime_v2.journal import RuntimeJournal


def test_release_hold_preserves_running_and_realtime_and_restores_real_due(tmp_path):
    path=Path(__file__).parents[1]/'scripts/ops/storage_release_due_hold.py'
    spec=importlib.util.spec_from_file_location('due_hold',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    now=datetime.now(UTC);journal=RuntimeJournal(tmp_path/'r.db',clock=lambda:now)
    for identity,kind in [('maintenance','MAINTENANCE'),('running','MAINTENANCE'),('case','CASE'),('w3','W3')]:journal.put_task(identity,'MU',kind,{})
    journal.claim('running')
    module.hold(journal.path,now=now)
    journal.put_task('later','MU','MAINTENANCE',{})
    assert journal.get_task('maintenance')['due_at']>now.isoformat()
    assert journal.get_task('later')['due_at']>now.isoformat()
    for identity in ('running','case','w3'):assert journal.get_task(identity)['due_at']==now.isoformat()
    journal.claim('case')
    module.restore(journal.path)
    for identity in ('maintenance','later'):assert journal.get_task(identity)['due_at']==now.isoformat()
    with journal.transaction(write=False,expand=False) as db:assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'storage_release_due_hold_%'").fetchone()
