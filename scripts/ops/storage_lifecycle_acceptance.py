"""Bounded read-only deployment evidence; does not start workflows or scan content trees."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from datetime import UTC,datetime


def read(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)


def main():
    from doxagent.settings import DoxAgentSettings
    settings=DoxAgentSettings()
    result={'at':datetime.now(UTC).isoformat(),'free_bytes':shutil.disk_usage('/data').free,'databases':{}}
    paths={'runtime':settings.persistent_runtime_v2_sqlite_path,'research':settings.codex_runtime_sqlite_path,'initialization':settings.ticker_initialization_control_path}
    for name,path in paths.items():
        path=Path(path)
        if not path.is_file():result['databases'][name]={'missing':str(path)};continue
        with closing(read(path)) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            value={'bytes':path.stat().st_size,'wal_bytes':Path(str(path)+'-wal').stat().st_size if Path(str(path)+'-wal').exists() else 0,'free_pages':db.execute('PRAGMA freelist_count').fetchone()[0]}
            if name=='initialization':
                value['o3_control']=db.execute('SELECT enabled,revision FROM maintenance_control').fetchall() if 'maintenance_control' in tables else 'MISSING'
            if name=='runtime':
                value['maintenance_latest']=db.execute("SELECT id,ticker,status,updated_at,json_extract(receipt,'$.o3_outcome') FROM runtime_tasks WHERE kind='MAINTENANCE' ORDER BY updated_at DESC LIMIT 5").fetchall()
                value['resource_ref_progress']=db.execute('SELECT key,value FROM runtime_resource_ref_meta').fetchall() if 'runtime_resource_ref_meta' in tables else 'NOT_DEPLOYED'
                value['process_exit_due']=db.execute("SELECT run_id,state,retry_at,error FROM runtime_resource_exit WHERE state IN ('PENDING','DELETING') ORDER BY state,retry_at,not_before LIMIT 5").fetchall() if 'runtime_resource_exit' in tables else 'NOT_DEPLOYED'
            if name=='research':
                value['shared_policy_enabled']=(path.parent/'.d3-shared-policy-ready').is_file()
                value['d3_process_due']=db.execute('SELECT run_id,due,error FROM codex_document3_process_exit ORDER BY due,run_id LIMIT 5').fetchall() if 'codex_document3_process_exit' in tables else 'NOT_DEPLOYED'
            if 'v2_source_outbox' in tables:value['expiry_index']=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_source_outbox_expiry'").fetchone())
            result['databases'][name]=value
    worker=Path(settings.codex_workspace_root)/'worker-jobs.sqlite3'
    with closing(read(worker)) as db:
        result['active_worker_jobs']=db.execute("SELECT id,status,json_extract(payload,'$.run_id'),json_extract(payload,'$.started_at'),json_extract(request,'$.node') FROM jobs WHERE status IN ('queued','running') ORDER BY sequence LIMIT 12").fetchall()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
