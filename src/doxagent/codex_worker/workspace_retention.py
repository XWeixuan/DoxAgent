"""Retire completed research inputs while keeping adopted outputs and evidence."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from doxagent.v2_read.maintenance import atomic_json
from .session_retention import business_holds


def paths_in(value,root,workspace_root):
    if isinstance(value,dict):
        run=value.get('run_id')
        if isinstance(run,str) and (workspace_root/run).resolve().parent==workspace_root.resolve():root=workspace_root/run
        for item in value.values():yield from paths_in(item,root,workspace_root)
    elif isinstance(value,list):
        for item in value:yield from paths_in(item,root,workspace_root)
    elif isinstance(value,str) and len(value)<4096:
        # ArtifactRef/source/citation paths, including references from another
        # run in the same workspace. Text bodies are not filesystem paths.
        try:
            path=Path(value)
            if not path.is_absolute():path=root/path
            path=path.resolve()
            if path.is_relative_to(root.resolve()) and path.is_file():yield path
        except (OSError,ValueError):pass


def collect(workspaces,jobs,settings,*,now=None,limit=4,dry_run=False):
    from doxagent.trade_execution.worker import WriterLock
    from doxagent.persistent_runtime_v2.resource_gc import opened_below
    now=now or datetime.now(UTC);report=dict(at=now.isoformat(),runs=[],errors=[])
    source=Path(settings.codex_runtime_sqlite_path)
    if not source.is_file():return report
    from doxagent.codex_runtime.immutable_evidence import collect_unused
    if not dry_run:report['unused_adoption_contents']=collect_unused(source.parent/'adoption-contents')
    root=workspaces.root
    with WriterLock(root/'workspace-retirement'):
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            published={run:json.loads(raw) for run,raw in db.execute("SELECT run_id,payload_json FROM codex_runtime_records WHERE record_type='bundles' AND json_extract(payload_json,'$.status')='published'")}
            evidence=list(db.execute("SELECT run_id,payload_json FROM codex_runtime_records WHERE record_type IN ('bundles','artifacts','documents','citations','sources')"))
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'codex_local_sources' in tables:evidence+=list(db.execute('SELECT run_id,source_metadata_json FROM codex_local_sources'))
            if 'codex_local_citation_manifests' in tables:evidence+=list(db.execute('SELECT run_id,manifest_json FROM codex_local_citation_manifests'))
        holds=business_holds(settings,now=now);held={v for k,v in holds if k=='run'}
        with closing(jobs.store.connect()) as db:
            rows=list(db.execute("SELECT status,json_extract(payload,'$.run_id'),coalesce(json_extract(payload,'$.finished_at'),json_extract(payload,'$.updated_at')),json_extract(request,'$.node'),json_extract(request,'$.task_phase') FROM jobs"))
        groups={}
        for status,run,at,node,phase in rows:
            if not run:continue
            groups.setdefault(run,[]).append((status,at,node,phase))
        for run,bundle in sorted(published.items()):
            if len(report['runs'])>=limit:break
            owners=groups.get(run,[]);directory=root/run
            if not owners or not directory.is_dir() or directory.is_symlink():continue
            if run.startswith(('w3-','runtime-')) or any(node=='persistent_runtime_w3' or phase in {'w3_template','trade_decision','post_decision_operations'} for _,_,node,phase in owners):continue
            if any(run==h or run.startswith(h+'-') for h in held):continue
            if any(status not in {'succeeded','failed','cancelled'} or not at or now-datetime.fromisoformat(at)<timedelta(days=14) for status,at,_,_ in owners):continue
            marker=directory/'audit'/'retained-assets.json'
            if marker.is_file() and json.loads(marker.read_text()).get('complete'):continue
            try:
                retained=set();artifacts=[]
                for owner,raw in evidence:
                    value=json.loads(raw)
                    retained.update(paths_in(value,root/owner,root))
                # Publication hashes are the authority. A candidate release by
                # itself is insufficient to retire an unfinished workflow.
                for artifact in bundle.get('artifacts',[]):
                    path=(directory/artifact['relative_path']).resolve()
                    if not path.is_relative_to(directory.resolve()) or not path.is_file():raise ValueError('adopted artifact is missing')
                    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    if digest!=artifact['sha256']:raise ValueError('adopted artifact checksum mismatch')
                    artifacts.append(dict(path=artifact['relative_path'],sha256=digest));retained.add(path)
                if not artifacts:continue
                candidates=[]
                for area in [directory/'context',*directory.glob('attempts/*/input')]:
                    if not area.is_dir() or area.is_symlink():continue
                    for path in area.rglob('*'):
                        if not path.is_file() or path.is_symlink() or path.resolve() in retained:continue
                        if not path.resolve().is_relative_to(directory.resolve()):continue
                        # Observation databases and control contracts remain
                        # independently readable even after input/session exit.
                        if path.name in {'task.json','context.json','AGENTS.md','agent.md','skill.md','manifest.json'} or path.name.endswith(('.db','.sqlite','.sqlite3','-wal','-shm')):continue
                        candidates.append(path)
                if opened_below(directory):continue
                receipt=dict(run=run,publication=artifacts,retained_reference_files=len(retained),files=0,allocated=0,at=now.isoformat(),complete=False)
                if dry_run:
                    receipt.update(files=len(candidates),allocated=sum(getattr(p.stat(),'st_blocks',(p.stat().st_size+511)//512)*512 for p in candidates));report['runs'].append(receipt);continue
                # Serialize with Worker submission metadata, then take its file
                # store lock. No live request can race a terminal input exit.
                with closing(jobs.store.connect()) as db,db,workspaces._lock:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute("SELECT 1 FROM jobs WHERE json_extract(payload,'$.run_id')=? AND status IN ('queued','running') LIMIT 1",(run,)).fetchone():continue
                    again={v for k,v in business_holds(settings,now=now) if k=='run'}
                    if any(run==h or run.startswith(h+'-') for h in again):continue
                    db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('workspace_retirement:'+run,now.isoformat()))
                    atomic_json(marker,receipt)
                    for path in candidates:
                        info=path.stat();path.unlink();receipt['files']+=1;receipt['allocated']+=getattr(info,'st_blocks',(info.st_size+511)//512)*512
                    receipt['complete']=True;atomic_json(marker,receipt)
                report['runs'].append(receipt)
            except (OSError,ValueError,sqlite3.Error) as exc:report['errors'].append(dict(run=run,error=str(exc)[:250]))
    return report
