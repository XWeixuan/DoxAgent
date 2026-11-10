"""Retire completed research inputs while keeping adopted outputs and evidence."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import re

from doxagent.v2_read.maintenance import atomic_json
from .session_retention import run_held


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


def family_root(run,d3_roots):
    matches=[root for root in d3_roots if run==root or run.startswith(root+'-')]
    if matches:return max(matches,key=len)
    runtime=re.match(r'^(runtime-maintain-[0-9a-f]{24})(?:-|$)',run)
    return runtime[1] if runtime else run


def family_jobs(db,run):
    return list(db.execute("SELECT status,json_extract(payload,'$.run_id'),coalesce(json_extract(payload,'$.finished_at'),json_extract(payload,'$.updated_at')),json_extract(request,'$.node'),json_extract(request,'$.task_phase') FROM jobs WHERE json_extract(payload,'$.run_id')=? OR (json_extract(payload,'$.run_id')>=? AND json_extract(payload,'$.run_id')<?)",(run,run+'-',run+'.')))


def collect(workspaces,jobs,settings,*,now=None,limit=4,dry_run=False,run_ids=None):
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
            from doxagent.v2_read.native_content import NativeContent
            codec=NativeContent(source)
            published={run:codec.decode(json.loads(raw)) for run,raw in db.execute("SELECT run_id,payload_json FROM codex_runtime_records WHERE record_type='bundles' AND json_extract(payload_json,'$.status')='published'")}
            # Bodies are canonical content, never filesystem references. Do not
            # expand every published document/source body on each owner pass.
            evidence=list(db.execute("SELECT run_id,json_remove(payload_json,'$.content_text','$.captured_text') FROM codex_runtime_records WHERE record_type IN ('bundles','artifacts','documents','citations','sources')"))
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            d3_roots={r[0] for r in db.execute('SELECT run_id FROM codex_document3_v21_runs')} if 'codex_document3_v21_runs' in tables else set()
            if 'codex_local_sources' in tables:evidence+=list(db.execute('SELECT run_id,source_metadata_json FROM codex_local_sources'))
            if 'codex_local_citation_manifests' in tables:evidence+=list(db.execute('SELECT run_id,manifest_json FROM codex_local_citation_manifests'))
        with closing(jobs.store.connect()) as db:
            previous=db.execute("SELECT value FROM metadata WHERE key='workspace_exit_cursor'").fetchone()
            cursor=int(previous[0]) if previous else 0
            page=list(db.execute("SELECT sequence,json_extract(payload,'$.run_id') FROM jobs WHERE sequence>? ORDER BY sequence LIMIT 256",(cursor,))) if run_ids is None else [(0,r) for r in dict.fromkeys(run_ids)]
            if len(page)>256:raise ValueError('workspace candidate page exceeds 256')
            rows=[]
            for run in dict.fromkeys(r[1] for r in page if r[1]):
                rows.extend(db.execute("SELECT status,json_extract(payload,'$.run_id'),coalesce(json_extract(payload,'$.finished_at'),json_extract(payload,'$.updated_at')),json_extract(request,'$.node'),json_extract(request,'$.task_phase') FROM jobs WHERE json_extract(payload,'$.run_id')=?",(run,)))
        groups={};families={};family_cache={}
        for status,run,at,node,phase in rows:
            if not run:continue
            groups.setdefault(run,[]).append((status,at,node,phase))
        with closing(jobs.store.connect()) as db:
            for run in list(groups):
                parent=family_root(run,d3_roots);families[run]=parent
                if parent!=run:
                    if parent not in family_cache:family_cache[parent]=[(s,at,n,p) for s,_,at,n,p in family_jobs(db,parent)]
                    owners=family_cache[parent]
                    groups[run]=owners
                    if parent in d3_roots:groups.setdefault(parent,owners);families[parent]=parent
        # A D3 parent workspace can have no direct model Jobs. It still owns
        # prepared inputs and inherits the real terminal Jobs of its children.
        groups=dict(sorted(groups.items(),key=lambda item:families.get(item[0],item[0])!=item[0]))
        # A failed/cancelled run may never publish. Its terminal process inputs
        # still have an exit; publication is authority for outputs, not a
        # prerequisite for retiring unreferenced copies in input directories.
        retained=set()
        for owner,raw in evidence:
            retained.update(paths_in(codec.decode(json.loads(raw)),root/owner,root))
        for run in groups:
            if len(report['runs'])>=limit:break
            authority_run=families.get(run,run) if families.get(run,run) in published else run
            bundle=published.get(authority_run,{})
            owners=groups[run];directory=root/run
            if directory.resolve().parent!=root.resolve() or not directory.is_dir() or directory.is_symlink():continue
            if run.startswith(('w3-','persistent-runtime-w3-')) or any(node=='persistent_runtime_w3' or phase in {'w3_template','trade_decision','post_decision_operations'} for _,_,node,phase in owners):continue
            if run_held(settings,run) or run_held(settings,families.get(run,run)):continue
            if any(status not in {'succeeded','failed','cancelled'} or not at for status,at,_,_ in owners):continue
            settlement=None
            with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_v21_runs'").fetchone():
                    row=db.execute("SELECT json_extract(payload,'$.settlement.at'),json_extract(payload,'$.candidate_handoff') IS NOT NULL FROM codex_document3_v21_runs WHERE run_id=?",(authority_run,)).fetchone()
                    if row and (row[0] or row[1]) and authority_run in published:
                        settlement=row[0] or bundle.get('published_at') or bundle.get('updated_at')
            runtime_path=getattr(settings,'persistent_runtime_v2_sqlite_path',None)
            if not settlement and runtime_path and Path(runtime_path).is_file():
                task_run="coalesce(json_extract(receipt,'$._gc_owner.run_id'),json_extract(receipt,'$.frame.run_id'))"
                with closing(sqlite3.connect(Path(runtime_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    row=db.execute('SELECT updated_at FROM runtime_tasks WHERE '+task_run+"=? AND status='SUCCEEDED' LIMIT 1",(family_root(run,set()),)).fetchone()
                    if row:settlement=row[0]
            days=1 if settlement else (3 if all(s=='cancelled' for s,_,_,_ in owners) else 7 if all(s=='succeeded' for s,_,_,_ in owners) else 14)
            if any(now-datetime.fromisoformat(at)<timedelta(days=days) for _,at,_,_ in owners):continue
            if settlement and now-datetime.fromisoformat(settlement)<timedelta(days=1):continue
            marker=directory/'audit'/'retained-assets.json'
            revision=hashlib.sha256(json.dumps([owners,authority_run,settlement],sort_keys=True).encode()).hexdigest()
            if marker.is_file():
                old=json.loads(marker.read_text())
                if old.get('complete') and old.get('owner_revision')==revision:continue
            try:
                artifacts=[]
                # Published outputs must match their authority. Unpublished
                # outputs/audit/Observation remain untouched; only terminal,
                # unreferenced process inputs below are candidates.
                for artifact in bundle.get('artifacts',[]):
                    authority_directory=root/authority_run
                    path=(authority_directory/artifact['relative_path']).resolve()
                    if not path.is_relative_to(authority_directory.resolve()) or not path.is_file():raise ValueError('adopted artifact is missing')
                    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    if digest!=artifact['sha256']:raise ValueError('adopted artifact checksum mismatch')
                    artifacts.append(dict(path=artifact['relative_path'],sha256=digest));retained.add(path)
                if authority_run in published and not artifacts:continue
                candidates=[]
                for area in [directory/'context',*directory.glob('attempts/*/input')]:
                    if not area.is_dir() or area.is_symlink():continue
                    for path in area.rglob('*'):
                        if not path.is_file() or path.is_symlink() or path.resolve() in retained:continue
                        if not path.resolve().is_relative_to(directory.resolve()):continue
                        # Observation databases and control contracts remain
                        # independently readable even after input/session exit.
                        if path.name.endswith(('.db','.sqlite','.sqlite3','-wal','-shm')):continue
                        if path.name in {'task.json','context.json','AGENTS.md','agent.md','skill.md','manifest.json'} and (not settlement or path.stat().st_size<=65536):continue
                        candidates.append(path)
                duplicate_outputs=0
                if settlement:
                    published_hashes={a['sha256'] for a in artifacts}
                    for path in (directory/'output'/'final').glob('*'):
                        if not path.is_file() or path.is_symlink() or path.resolve() in retained:continue
                        with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
                        if sha in published_hashes:candidates.append(path);duplicate_outputs+=1
                if opened_below(directory):continue
                receipt=dict(run=run,publication=artifacts,owner_revision=revision,publication_run=authority_run,retirement_basis='settled' if settlement else 'published' if run in published else 'terminal-unpublished-inputs',retained_reference_files=len(retained),duplicate_outputs=duplicate_outputs,files=0,allocated=0,at=now.isoformat(),complete=False)
                if dry_run:
                    receipt.update(files=len(candidates),allocated=sum(getattr(p.stat(),'st_blocks',(p.stat().st_size+511)//512)*512 for p in candidates));report['runs'].append(receipt);continue
                # Serialize with Worker submission metadata, then take its file
                # store lock. No live request can race a terminal input exit.
                with closing(jobs.store.connect()) as db,db,workspaces._lock:
                    db.execute('BEGIN IMMEDIATE')
                    family=families.get(run,run)
                    current=family_jobs(db,family)
                    if any(status in {'queued','running'} for status,*_ in current):continue
                    if run_held(settings,run) or run_held(settings,family):continue
                    fenced={run,family,*(r[1] for r in current)}
                    db.executemany('INSERT OR REPLACE INTO metadata VALUES(?,?)',[('workspace_retirement:'+r,now.isoformat()) for r in fenced])
                    atomic_json(marker,receipt)
                # Submission is fenced by the retirement metadata; unlink outside writer locks.
                for path in candidates:
                    info=path.stat();path.unlink();receipt['files']+=1;receipt['allocated']+=getattr(info,'st_blocks',(info.st_size+511)//512)*512
                abandoned=not settlement and all(status=='cancelled' for status,_,_,_ in owners)
                d3_owner=families.get(run,run) if abandoned else authority_run
                if settlement or abandoned and d3_owner in d3_roots:
                    from doxagent.workflows.codex_document3.state_v21 import StateV21
                    receipt['d3']=StateV21(source).retire(d3_owner,now=now,
                        authority='cancelled-terminal-owner' if abandoned else 'published-candidate',abandoned=abandoned)
                receipt['complete']=True;atomic_json(marker,receipt)
                report['runs'].append(receipt)
            except (OSError,ValueError,sqlite3.Error) as exc:report['errors'].append(dict(run=run,error=str(exc)[:250]))
        if not dry_run and run_ids is None:
            # If the output limit stopped the page, revisit it; completed runs are cheap skips.
            next_cursor=cursor if len(report['runs'])>=limit else page[-1][0] if page else 0
            with closing(jobs.store.connect()) as db,db:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('workspace_exit_cursor',?)",(str(next_cursor),))
    return report
