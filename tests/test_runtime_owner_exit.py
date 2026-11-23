from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from doxagent.codex_worker.job_store import JobStore
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.owner_exit import drain


def test_terminal_event_enqueues_and_resumes_partial_owner_exit(tmp_path):
    now=[datetime.now(UTC)]
    journal=RuntimeJournal(tmp_path/'runtime.db',clock=lambda:now[0])
    workspace=tmp_path/'workspaces';JobStore(workspace)
    settings=SimpleNamespace(codex_workspace_root=str(workspace))
    run='runtime-maintain-'+'a'*24
    journal.put_task('task','MU','MAINTENANCE',{})
    task=journal.claim('task')
    journal.checkpoint(task,frame={'run_id':run,'base':{'artifacts':{'event_library':{'root':str(tmp_path/'business'),'version':1}}}},o3_outcome='SKIPPED_BY_CONTROL')
    journal.finish(task)
    art=tmp_path/'runtime_artifacts'/run
    for folder in (art,workspace/run,workspace/(run+'-o2')):
        folder.mkdir(parents=True)
        (folder/'process.json').write_text('diagnostic')
    business=tmp_path/'business';business.mkdir();(business/'evidence').write_text('keep')
    assert not drain(journal,settings)['processed']
    now[0]+=timedelta(hours=25)
    assert drain(journal,settings,seconds=5)['processed'][0]['done']
    assert not art.exists() and not (workspace/(run+'-o2')).exists()
    assert (business/'evidence').read_text()=='keep'
    assert journal.get_task('task')['receipt']['process_retired']
    assert not drain(journal,settings)['processed']
