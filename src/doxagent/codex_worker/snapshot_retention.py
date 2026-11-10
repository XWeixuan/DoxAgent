"""Pre-node Initialization snapshots: private forks, bounded recovery responsibility."""
from contextlib import closing,ExitStack
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


def protection(db, runtime_path, *, primary=True):
    runs={r[0]:dict(ticker=r[1],status=r[2],at=r[3],manual=r[4]) for r in db.execute(
        "SELECT id,ticker,json_extract(payload,'$.status'),coalesce(json_extract(payload,'$.updated_at'),json_extract(payload,'$.created_at')),json_extract(payload,'$.manual_resume_required') FROM initialization_runs")}
    held={run for run,value in runs.items() if value['status'] not in TERMINAL or value['manual']}
    current=set()
    if runtime_path and Path(runtime_path).is_file():
        with closing(sqlite3.connect(Path(runtime_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as runtime:
            current.update(r[0] for r in runtime.execute("SELECT json_extract(payload,'$.initialization_id') FROM v2_ticker_control"))
    for (revision,) in db.execute('SELECT revision_id FROM ticker_active_revision') if primary else ():
        seen=set()
        while revision and revision not in seen:
            seen.add(revision)
            if revision.endswith('-activation') and revision[:-11] in runs:
                current.add(revision[:-11]);break
            row=db.execute('SELECT base_revision FROM activation_revisions WHERE revision_id=?',(revision,)).fetchone()
            revision=row[0] if row else None
    held.update(current)
    # Main keeps its accepted predecessor. An ops refresh's local head is not
    # a permanent production pin; live/manual/rerun and Runtime pins still hold it.
    previous={}
    for run,value in runs.items() if primary else ():
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
        progress=workspaces.root/'.init-retention.json'
        try:after=json.loads(progress.read_text()).get('cursor','')
        except (OSError,ValueError):after=''
        page=sorted((p for p in owners.glob('*.json') if p.name>after),key=lambda p:p.name)[:32]
        # Complete an interrupted filesystem exit; its durable RETIRED marker
        # already prevents new forks/recreation under the same identity.
        if not dry_run and retired.is_dir():
            for target in retired.iterdir():
                marker=owners/(target.name+'.json')
                if re.fullmatch('[a-f0-9]{32}',target.name) and target.is_dir() and not target.is_symlink() and marker.is_file() and json.loads(marker.read_text()).get('state')=='RETIRED':shutil.rmtree(target)
        from .initialization_owners import control_paths
        primary=Path(path).resolve();data=primary.parent.parent
        with ExitStack() as stack:
            ledgers={}
            for candidate in control_paths(settings):
                connection=stack.enter_context(closing(sqlite3.connect(candidate,timeout=2)))
                names={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'initialization_runs','initialization_attempts','initialization_nodes'}<=names:continue
                ledgers[candidate]=(connection,*protection(connection,settings.persistent_runtime_v2_sqlite_path,primary=candidate==primary))
            report['ledgers']=[p.relative_to(data).as_posix() for p in ledgers]
            all_held=set().union(*(v[2] for v in ledgers.values()))
            all_pins=set().union(*(v[3] for v in ledgers.values()))
            for marker in page:
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
                    matches=[]
                    for ledger,(connection,candidates,_,_) in ledgers.items():
                        match=connection.execute('SELECT run_id FROM initialization_attempts WHERE execution_id=?',(snapshot,)).fetchone()
                        if match and match[0] in candidates:matches.append((ledger,match[0]))
                    if not matches:
                        parent=value.get('initialization_id')
                        if parent:matches=[(ledger,parent) for ledger,(_,candidates,_,_) in ledgers.items() if parent in candidates]
                    if not matches:
                        matches=[(ledger,parent) for ledger,(_,candidates,_,_) in ledgers.items()
                                 for parent in candidates if run==parent or run.startswith(parent+'-')]
                        if matches:
                            length=max(len(parent) for _,parent in matches)
                            matches=[v for v in matches if len(v[1])==length]
                    declared=value.get('initialization_control_path')
                    if declared:
                        exact=[v for v in matches if v[0].relative_to(data).as_posix()==declared]
                        if exact:matches=exact
                    if len(matches)!=1:
                        report['unknown']+=1
                        report.setdefault('unknown_samples',[]).append(dict(workspace=run,snapshot=snapshot,reason='ambiguous ledgers' if matches else 'owner not found'))
                        continue
                    ledger,parent=matches[0];db,runs,held,pinned=ledgers[ledger]
                    owner=runs[parent]
                    if not dry_run and (value.get('initialization_id')!=parent or value.get('initialization_control_path')!=ledger.relative_to(data).as_posix()):
                        value.update(initialization_id=parent,initialization_control_path=ledger.relative_to(data).as_posix());atomic_json(marker,value)
                    at=owner['at'];age=timedelta(days=14 if owner['status']=='FAILED' else 2)
                    if parent in all_held or identity in all_pins or not at or now-datetime.fromisoformat(at)<age:report['protected']+=1;continue
                    target=workspaces.root/'.init'/identity
                    if not target.is_dir() or target.is_symlink():continue
                    if opened_below(target):continue
                    if dry_run:report['retired'].append(identity);continue
                    # Keep the Source writer fence short: publish the tombstone
                    # and rename only; recursive deletion is outside the lock.
                    with db,workspaces._lock:
                        db.execute('BEGIN IMMEDIATE')
                        again,current,pins=protection(db,settings.persistent_runtime_v2_sqlite_path,primary=ledger==primary)
                        for other,(connection,_,_,_) in ledgers.items():
                            if other==ledger:continue
                            _,held_elsewhere,pinned_elsewhere=protection(connection,settings.persistent_runtime_v2_sqlite_path,primary=other==primary)
                            current.update(held_elsewhere);pins.update(pinned_elsewhere)
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
            atomic_json(progress,{'cursor':after if len(report['retired'])>=limit else page[-1].name if page else '',
                                  'at':now.isoformat(),'scanned':len(page),'unknown':report.get('unknown_samples',[])})
            with workspaces._lock:report['unused_contents']=collect_unused(workspaces.root/'.init-contents')
    return report
