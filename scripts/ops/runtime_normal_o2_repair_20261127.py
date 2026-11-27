"""One-time bounded, identity-preserving O2 retirement and saved Ops recovery.

Run in the released Scheduler image. Default is a read-only audit; --apply
requires Scheduler drained and Transport Maintenance stopped. No model client.
"""
import argparse
from datetime import datetime, UTC
import json
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.normal_o2 import supplemental
from doxagent.persistent_runtime_v2.resource_refs import backfill
from doxagent.persistent_runtime_v2.w3_contracts import parse_w3_operations


def run(settings, *, apply=False):
    repo=SQLitePersistentRuntimeV2Repository(settings.persistent_runtime_v2_sqlite_path) if apply else None
    journal=RuntimeJournal(settings.persistent_runtime_v2_sqlite_path,initialize=False)
    report=dict(at=datetime.now(UTC).isoformat(),apply=apply,retired=[],reconcile=[],recovered=[],blocked=[])
    # Each page is outside the writer; Native expansion/validation never holds it.
    after=''
    while True:
        with journal.transaction(write=False,expand=False) as db:
            rows=db.execute("SELECT id,inputs,receipt,status,token,lease_until FROM runtime_tasks "
                "WHERE kind='MAINTENANCE' AND id>? AND status IN ('PENDING','RUNNING','HELD','FAILED') "
                "AND json_extract(receipt,'$.maintenance_retired') IS NULL ORDER BY id LIMIT 32",(after,)).fetchall()
        if not rows:break
        for row in rows:
            task=dict(row);task['inputs']=json.loads(task['inputs'])
            if not supplemental(task):continue
            receipt=json.loads(journal.content.text(row['receipt']))
            if row['status']=='RUNNING' and (row['lease_until'] or '')>journal.clock().isoformat():
                report['blocked'].append(dict(task=row['id'],reason='LIVE_TASK_OWNER'));continue
            # A frozen frame can contain accepted/unknown Jobs or local publish
            # work. Resume its original identity under the no-new-dispatch guard.
            frame=receipt.get('frame') or {}
            resumable=bool(receipt.get('o2'))
            if frame and not receipt.get('process_retired'):
                prefix='runtime-worker:'+frame['run_id']+':'
                with journal.transaction(write=False,expand=False) as db:
                    requests=db.execute("SELECT key,payload FROM runtime_values WHERE namespace='worker_requests' "
                        "AND key>=? AND key<? ORDER BY key",(prefix,prefix+'\uffff')).fetchall()
                with closing(sqlite3.connect(Path(settings.codex_workspace_root,'worker-jobs.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as jobs:
                    for key,payload in requests:
                        request=json.loads(journal.content.text(payload))
                        if request.get('retired_process'):continue
                        identity=hashlib.sha256((request['run_id']+':'+request['idempotency_key']).encode()).hexdigest()
                        job=jobs.execute('SELECT status FROM jobs WHERE id=?',(identity,)).fetchone()
                        # Authority lookup: unknown storage errors abort; only
                        # actual live/paid-success Jobs need original-run replay.
                        if job and job[0] in ('queued','running','succeeded'):
                            resumable=True
            if resumable and frame and not receipt.get('process_retired'):
                report['reconcile'].append(row['id'])
                if apply:
                    with journal.transaction(expand=False,operation='repair.o2.reconcile') as db:
                        db.execute("UPDATE runtime_tasks SET status='PENDING',owner=NULL,lease_until=NULL,"
                            "generation=generation+1,due_at=?,receipt=json_set(receipt,'$.normal_window_retirement_pending',1) "
                            "WHERE id=? AND status=? AND token=? AND receipt=?",
                            (journal.clock().isoformat(),row['id'],row['status'],row['token'],row['receipt']))
                continue
            report['retired'].append(row['id'])
            if apply:
                with journal.transaction(expand=False,operation='repair.o2.retire') as db:
                    db.execute("UPDATE runtime_tasks SET status='FAILED',owner=NULL,lease_until=NULL,receipt=json_set("
                        "receipt,'$.maintenance_retired',?,'$.administrative_reason','SUPPLEMENTAL_O2_DISABLED') "
                        "WHERE id=? AND status=? AND token=? AND receipt=?",
                        (journal.clock().isoformat(),row['id'],row['status'],row['token'],row['receipt']))
        after=rows[-1]['id']
    with journal.transaction(write=False,expand=False) as db:
        cases=db.execute("SELECT s.case_id,s.result_json,s.thread_id,s.error,e.effect_id,e.status,e.payload_json "
            "FROM runtime_v2_w3_stages s JOIN runtime_v2_effects e USING(case_id) "
            "JOIN runtime_v2_w3_stages t ON t.case_id=s.case_id AND t.phase='trade_decision' "
            "WHERE s.phase='post_decision_operations' AND s.status='FAILED' AND s.result_json IS NOT NULL "
            "AND s.error LIKE '%HISTORY_RETIRED%' AND t.status='COMMITTED' AND s.thread_id=t.thread_id "
            "AND e.effect_type='w3_post_decision' AND e.status='FAILED' ORDER BY s.case_id LIMIT 100").fetchall()
    for row in cases:
        try:
            # A successful paid invocation on this exact private Thread is
            # required, and is retained as the audit authority.
            with journal.transaction(write=False,expand=False) as db:
                proof=db.execute("SELECT key,payload FROM runtime_values INDEXED BY w3_legacy_invocation_thread "
                    "WHERE namespace='worker_invocations' AND json_extract(payload,'$.node')='persistent_runtime_w3' "
                    "AND json_extract(payload,'$.job.thread_id')=?",(row['thread_id'],)).fetchall()
                raw=db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(row['case_id'],)).fetchone()
            case=json.loads(journal.content.text(raw[0]))
            successful=[key for key,payload in proof if (lambda x:
                x.get('task_phase')=='post_decision_operations' and x.get('job',{}).get('status')=='succeeded')(
                    json.loads(journal.content.text(payload)))]
            if not successful:raise ValueError('SUCCESSFUL_ORIGINAL_OPERATIONS_RECEIPT_MISSING')
            result=json.loads(journal.content.text(row['result_json']))
            parse_w3_operations(result,version=case['w3_contract_version'],allow_legacy_pin_watch=True)
            entry=dict(case_id=row['case_id'],effect_id=row['effect_id'],receipts=successful,
                       original_error=row['error'])
            if apply:
                audit=journal.content.encode(entry)
                with journal.transaction(expand=False,operation='repair.ops.saved_result') as db:
                    changed=db.execute("UPDATE runtime_v2_w3_stages SET status='RESULT_SAVED' "
                        "WHERE case_id=? AND phase='post_decision_operations' AND status='FAILED' "
                        "AND result_json=? AND error=? AND EXISTS(SELECT 1 FROM runtime_v2_effects "
                        "WHERE effect_id=? AND status='FAILED' AND payload_json=?)",
                        (row['case_id'],row['result_json'],row['error'],row['effect_id'],row['payload_json'])).rowcount
                    if not changed:raise ValueError('SAVED_RESULT_CAS_CHANGED')
                    stamp=journal.clock().isoformat()
                    db.execute("UPDATE runtime_v2_effects SET status='PENDING_RETRY',available_at=?,updated_at=?,"
                        "payload_json=json_set(payload_json,'$.status','PENDING_RETRY','$.available_at',?,"
                        "'$.lease_until',NULL,'$.lease_token',NULL) WHERE effect_id=? AND status='FAILED'",
                        (stamp,stamp,stamp,row['effect_id']))
                    db.execute("INSERT OR IGNORE INTO runtime_values VALUES('history_retired_saved_result_recovery',?,?)",
                               (row['case_id'],audit))
            report['recovered'].append(entry)
        except (ValueError,KeyError) as exc:
            report['blocked'].append(dict(case_id=row['case_id'],reason=str(exc)))
    if apply:
        # One migration page per transaction, no full historical Case expansion.
        for _ in range(1000):
            if backfill(journal):break
        else:raise ValueError('REFERENCE_MIGRATION_DID_NOT_CONVERGE')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    result=run(DoxAgentSettings(),apply=args.apply)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:(len(v) if isinstance(v,list) else v) for k,v in result.items()}))
