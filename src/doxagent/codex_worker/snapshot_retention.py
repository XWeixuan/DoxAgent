"""Pre-node Initialization snapshots: private forks, bounded recovery responsibility."""
from contextlib import closing
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

from doxagent.v2_read.maintenance import atomic_json

TERMINAL={'SUCCEEDED','CANCELLED','FAILED'}


def protection(db, runtime_path):
    runs={r[0]:dict(ticker=r[1],status=r[2],at=r[3],manual=r[4]) for r in db.execute(
        "SELECT id,ticker,json_extract(payload,'$.status'),coalesce(json_extract(payload,'$.updated_at'),json_extract(payload,'$.created_at')),json_extract(payload,'$.manual_resume_required') FROM initialization_runs")}
    held={run for run,value in runs.items() if value['status'] not in TERMINAL or value['manual']}
    current=set()
    if runtime_path and Path(runtime_path).is_file():
        with closing(sqlite3.connect(Path(runtime_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as runtime:
            current.update(r[0] for r in runtime.execute("SELECT json_extract(payload,'$.initialization_id') FROM v2_ticker_control"))
    for (revision,) in db.execute('SELECT revision_id FROM ticker_active_revision'):
        seen=set()
        while revision and revision not in seen:
            seen.add(revision)
            if revision.endswith('-activation') and revision[:-11] in runs:
                current.add(revision[:-11]);break
            row=db.execute('SELECT base_revision FROM activation_revisions WHERE revision_id=?',(revision,)).fetchone()
            revision=row[0] if row else None
    held.update(current)
    # One accepted preceding initialization remains recoverable per ticker.
    previous={}
    for run,value in runs.items():
        if value['status']=='SUCCEEDED' and run not in current:
            old=previous.get(value['ticker'])
            if not old or (value['at'] or '')>(runs[old]['at'] or ''):previous[value['ticker']]=run
    held.update(previous.values())
    # An in-flight internal rerun may fork an older pre-node snapshot.
    snapshots=set()
    from doxagent.v2_read.native_content import NativeContent
    native=NativeContent(db.execute('PRAGMA database_list').fetchone()[2])
    for (raw,) in db.execute("SELECT n.payload FROM initialization_nodes n JOIN initialization_runs r ON r.id=n.run_id WHERE n.node_key='internal.rerun' AND json_extract(r.payload,'$.status') NOT IN ('SUCCEEDED','FAILED','CANCELLED')"):
        value=native.decode(json.loads(raw))
        snapshot=value.get('inputs',{}).get('_snapshot',{})
        workspace=snapshot.get('workspace_run_id',snapshot.get('run_id'))
        if workspace and snapshot.get('snapshot_id'):snapshots.add(hashlib.sha256(f"{workspace}:{snapshot['snapshot_id']}".encode()).hexdigest()[:32])
    return runs,held,snapshots


def collect(workspaces, jobs, settings, *, now=None, limit=8, dry_run=False):
    from doxagent.trade_execution.worker import WriterLock
    from doxagent.codex_runtime.immutable_evidence import collect_unused
    from doxagent.persistent_runtime_v2.resource_gc import opened_below
    now=now or datetime.now(UTC)
    report=dict(retired=[],protected=0,unknown=0,errors=[])
    path=settings.ticker_initialization_control_path
    if not path or not Path(path).is_file():return report
    owners=workspaces.root/'.init-owners';retired=workspaces.root/'.init-retired'
    if not owners.is_dir():return report
    with WriterLock(workspaces.root/'initialization-snapshot-retirement'):
        # Complete an interrupted filesystem exit; its durable RETIRED marker
        # already prevents new forks/recreation under the same identity.
        if not dry_run and retired.is_dir():
            for target in retired.iterdir():
                marker=owners/(target.name+'.json')
                if re.fullmatch('[a-f0-9]{32}',target.name) and target.is_dir() and not target.is_symlink() and marker.is_file() and json.loads(marker.read_text()).get('state')=='RETIRED':shutil.rmtree(target)
        with closing(sqlite3.connect(path,timeout=2)) as db:
            runs,held,pinned=protection(db,settings.persistent_runtime_v2_sqlite_path)
            for marker in owners.glob('*.json'):
                if len(report['retired'])>=limit:break
                try:
                    value=json.loads(marker.read_text())
                    run,snapshot=value['workspace_run_id'],value['snapshot_id']
                    if not run.startswith(('init-','d2ws-','rerun-')):raise ValueError('non-initialization snapshot owner')
                    identity=hashlib.sha256(f'{run}:{snapshot}'.encode()).hexdigest()[:32]
                    if marker.name!=identity+'.json':raise ValueError('snapshot owner identity mismatch')
                    if value.get('state')=='RETIRED':
                        # Crash after tombstone but before rename.
                        target=workspaces.root/'.init'/identity
                        if not dry_run and target.is_dir() and not target.is_symlink():
                            with workspaces._lock:
                                retired.mkdir(exist_ok=True);os.replace(target,retired/identity)
                            shutil.rmtree(retired/identity)
                        continue
                    parent=db.execute('SELECT run_id FROM initialization_attempts WHERE execution_id=?',(snapshot,)).fetchone()
                    if not parent or parent[0] not in runs:report['unknown']+=1;continue
                    parent=parent[0];owner=runs[parent]
                    at=owner['at'];age=timedelta(days=14 if owner['status']=='FAILED' else 2)
                    if parent in held or identity in pinned or not at or now-datetime.fromisoformat(at)<age:report['protected']+=1;continue
                    target=workspaces.root/'.init'/identity
                    if not target.is_dir() or target.is_symlink():continue
                    if opened_below(target):continue
                    if dry_run:report['retired'].append(identity);continue
                    # Keep the Source writer fence short: publish the tombstone
                    # and rename only; recursive deletion is outside the lock.
                    with db,workspaces._lock:
                        db.execute('BEGIN IMMEDIATE')
                        again,current,pins=protection(db,settings.persistent_runtime_v2_sqlite_path)
                        if parent in current or identity in pins or again[parent]!=owner:continue
                        with closing(jobs.store.connect()) as jobdb:
                            if jobdb.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') AND json_extract(payload,'$.run_id')=? LIMIT 1",(run,)).fetchone():continue
                        retired.mkdir(exist_ok=True)
                        value.update(state='RETIRED',initialization_id=parent,at=now.isoformat())
                        atomic_json(marker,value)
                        os.replace(target,retired/identity)
                    shutil.rmtree(retired/identity);report['retired'].append(identity)
                except (ValueError,OSError,sqlite3.Error) as exc:report['errors'].append(dict(owner=marker.name,error=str(exc)[:200]))
        if not dry_run:
            with workspaces._lock:report['unused_contents']=collect_unused(workspaces.root/'.init-contents')
    return report
