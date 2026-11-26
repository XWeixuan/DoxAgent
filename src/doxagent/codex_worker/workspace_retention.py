"""Retire completed research inputs while keeping adopted outputs and evidence."""
from contextlib import closing, nullcontext
from datetime import UTC,datetime,timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import re
import time
from itertools import chain
from uuid import uuid4

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


def retire_d3_copies(directory, staged, now):
    """Collapse exact aliases for one D3 owner whose Worker family is fenced."""
    from pathlib import PurePosixPath

    def owned_path(relative):
        parts = PurePosixPath(relative).parts
        if not parts or '..' in parts or PurePosixPath(relative).is_absolute():
            raise ValueError('invalid D3 publication path')
        target = directory.joinpath(*parts)
        if target.resolve() != target or not target.is_relative_to(directory):
            raise ValueError('linked or escaped D3 publication member')
        return target

    manifest = owned_path(staged['release_manifest_path'])
    if manifest.name != 'manifest.json' or manifest.parent.parent != directory/'published':
        raise ValueError('invalid D3 immutable release directory')
    entries = {item['relative_path']: item['sha256']
               for item in json.loads(manifest.read_bytes())['artifacts']}
    copied = 0
    for name, relative in staged['files'].items():
        digest = staged['hashes'][name]
        source = owned_path(relative)
        if not relative.startswith('artifacts/document3/releases/') or entries.get(relative) != digest:
            raise ValueError('stage and publication manifest disagree')
        release_member = manifest.parent / source.relative_to(directory/'artifacts')
        if release_member.resolve() != release_member or not release_member.is_file():
            raise ValueError('D3 published copy is unavailable')
        aliases = [release_member, owned_path('output/final/' + source.name)]
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('D3 publication checksum mismatch')
        for alias in aliases:
            if alias.resolve() != alias or not alias.is_file() or alias.samefile(source):
                continue
            with alias.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    if alias == release_member:
                        raise ValueError('D3 published copy checksum mismatch')
                    continue
            temporary = alias.with_name('.storage-alias-' + uuid4().hex)
            try:
                os.link(source, temporary)
                os.replace(temporary, alias)
            finally:
                temporary.unlink(missing_ok=True)
            copied += 1
    # This view is derived from StateV21, and Pilot can now export it on demand.
    owned_path('artifacts/document3/v21_state.json').unlink(missing_ok=True)
    receipt = {'at': now.isoformat(), 'aliases_shared': copied, 'manifest': staged['release_manifest_path']}
    atomic_json(owned_path('audit/d3-process-exit.json'), receipt)
    return receipt


def collect(workspaces,jobs,settings,*,now=None,limit=4,dry_run=False,run_ids=None,settled_grace=timedelta(days=1)):
    from doxagent.trade_execution.worker import WriterLock
    from doxagent.persistent_runtime_v2.resource_gc import opened_below
    now=now or datetime.now(UTC);report=dict(at=now.isoformat(),runs=[],errors=[])
    source=Path(settings.codex_runtime_sqlite_path)
    if not source.is_file():return report
    from doxagent.codex_runtime.immutable_evidence import collect_unused
    if not dry_run and run_ids is None:report['unused_adoption_contents']=collect_unused(source.parent/'adoption-contents')
    scope=None
    if run_ids is not None:
        scope=set(run_ids)
        with closing(jobs.store.connect()) as db:
            for parent in run_ids:
                scope.update(row[1] for row in family_jobs(db,parent) if row[1])
        if len(scope)>256:raise ValueError('workspace owner family exceeds bounded scope')
    restriction=' AND run_id IN (SELECT value FROM json_each(?))' if scope is not None else ''
    parameters=(json.dumps(sorted(scope)),) if scope is not None else ()
    root=workspaces.root
    with WriterLock(root/'workspace-retirement'):
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            from doxagent.v2_read.native_content import NativeContent
            codec=NativeContent(source)
            published={run:codec.decode(json.loads(raw)) for run,raw in db.execute("SELECT run_id,payload_json FROM codex_runtime_records WHERE record_type='bundles' AND json_extract(payload_json,'$.status')='published'"+restriction,parameters)}
            # Bodies are canonical content, never filesystem references. Do not
            # expand every published document/source body on each owner pass.
            evidence=list(db.execute("SELECT run_id,json_remove(payload_json,'$.content_text','$.captured_text') FROM codex_runtime_records WHERE record_type IN ('bundles','artifacts','documents','citations','sources')"+restriction,parameters))
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            d3_roots={r[0] for r in db.execute('SELECT run_id FROM codex_document3_v21_runs WHERE 1'+restriction,parameters)} if 'codex_document3_v21_runs' in tables else set()
            if 'codex_local_sources' in tables:evidence+=list(db.execute('SELECT run_id,source_metadata_json FROM codex_local_sources WHERE 1'+restriction,parameters))
            if 'codex_local_citation_manifests' in tables:evidence+=list(db.execute('SELECT run_id,manifest_json FROM codex_local_citation_manifests WHERE 1'+restriction,parameters))
        with closing(jobs.store.connect()) as db:
            previous=db.execute("SELECT value FROM metadata WHERE key='workspace_exit_cursor'").fetchone()
            cursor=int(previous[0]) if previous else 0
            page=list(db.execute("SELECT sequence,json_extract(payload,'$.run_id') FROM jobs WHERE sequence>? ORDER BY sequence LIMIT 256",(cursor,))) if run_ids is None else [(0,r) for r in sorted(scope)]
            if len(page)>256:raise ValueError('workspace candidate page exceeds 256')
            rows=[]
            for run in dict.fromkeys(r[1] for r in page if r[1]):
                rows.extend(db.execute("SELECT status,json_extract(payload,'$.run_id'),coalesce(json_extract(payload,'$.finished_at'),json_extract(payload,'$.updated_at')),json_extract(request,'$.node'),json_extract(request,'$.task_phase') FROM jobs WHERE json_extract(payload,'$.run_id')=?",(run,)))
        groups={};families={};family_cache={}
        for status,run,at,node,phase in rows:
            if not run:continue
            groups.setdefault(run,[]).append((status,at,node,phase))
        with closing(jobs.store.connect()) as db:
            for parent in (run_ids or []):
                if parent in d3_roots:
                    owners=[(s,at,n,p) for s,_,at,n,p in family_jobs(db,parent)]
                    if not owners and parent in published:
                        at=published[parent].get('published_at') or published[parent].get('updated_at')
                        owners=[('succeeded',at,None,None)]
                    groups[parent]=owners
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
        def held(run):
            if run_ids is not None and run.startswith('runtime-maintain-') and '-o3' in run:
                from doxagent.ticker_initialization.repository import InitializationRepository
                if not InitializationRepository(settings.ticker_initialization_control_path).o3_maintenance_enabled():return False
            return run_held(settings,run)
        for run in groups:
            if len(report['runs'])>=limit:break
            authority_run=families.get(run,run) if families.get(run,run) in published else run
            bundle=published.get(authority_run,{})
            owners=groups[run];directory=root/run
            if directory.resolve().parent!=root.resolve() or not directory.is_dir() or directory.is_symlink():continue
            if run.startswith(('w3-','persistent-runtime-w3-')) or any(node=='persistent_runtime_w3' or phase in {'w3_template','trade_decision','post_decision_operations'} for _,_,node,phase in owners):continue
            if held(run) or held(families.get(run,run)):continue
            if any(status not in {'succeeded','failed','cancelled'} or not at for status,at,_,_ in owners):continue
            settlement=None;typed_release=False;staged_files=set()
            with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='codex_document3_v21_runs'").fetchone():
                    row=db.execute("SELECT json_extract(payload,'$.settlement.at'),json_extract(payload,'$.candidate_handoff') IS NOT NULL FROM codex_document3_v21_runs WHERE run_id=?",(authority_run,)).fetchone()
                    staged_row=db.execute('SELECT version,receipt FROM codex_document3_v21_staged_sets WHERE run_id=?',(authority_run,)).fetchone()
                    if staged_row and bundle.get('handoff',{}).get('policy_set_version')==staged_row[0]:
                        typed_release=True;staged_files=set(json.loads(staged_row[1]).get('files',{}))
                    if row and (row[0] or row[1]) and authority_run in published:
                        settlement=row[0] or bundle.get('published_at') or bundle.get('updated_at')
            runtime_path=getattr(settings,'persistent_runtime_v2_sqlite_path',None)
            if not settlement and runtime_path and Path(runtime_path).is_file():
                task_run="coalesce(json_extract(receipt,'$._gc_owner.run_id'),json_extract(receipt,'$.frame.run_id'))"
                with closing(sqlite3.connect(Path(runtime_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    row=db.execute('SELECT updated_at FROM runtime_tasks WHERE '+task_run+"=? AND status='SUCCEEDED' LIMIT 1",(family_root(run,set()),)).fetchone()
                    if row:settlement=row[0]
            grace=settled_grace if typed_release and run_ids is not None else timedelta(days=1)
            days=grace.total_seconds()/86400 if settlement else (3 if all(s=='cancelled' for s,_,_,_ in owners) else 7 if all(s=='succeeded' for s,_,_,_ in owners) else 14)
            if any(now-datetime.fromisoformat(at)<timedelta(days=days) for _,at,_,_ in owners):continue
            if settlement and now-datetime.fromisoformat(settlement)<grace:continue
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
                    if not path.is_relative_to(authority_directory.resolve()):raise ValueError('adopted artifact escapes owner')
                    if typed_release:
                        # Typed staged release is recovery authority; old export
                        # byte equality is not a reason to reread huge files.
                        digest=artifact['sha256']
                    else:
                        if not path.is_file():raise ValueError('adopted artifact is missing')
                        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
                        if digest!=artifact['sha256']:raise ValueError('adopted artifact checksum mismatch')
                    artifacts.append(dict(path=artifact['relative_path'],sha256=digest));retained.add(path)
                if authority_run in published and not artifacts:continue
                candidates=[];detach_areas=[];more=False
                for area in chain([directory/'context'],directory.glob('attempts/*/input')):
                    if len(candidates)>=256:more=True;break
                    if not area.is_dir() or area.is_symlink():continue
                    if typed_release and settlement and run_ids is not None and not any(p.is_relative_to(area.resolve()) for p in retained):
                        if len(detach_areas)>=4:more=True;break
                        detach_areas.append(area);continue
                    for path in area.rglob('*'):
                        if len(candidates)>=256:more=True;break
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
                        if typed_release and path.name in staged_files:
                            candidates.append(path);duplicate_outputs+=1
                        elif not typed_release:
                            with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
                            if sha in published_hashes:candidates.append(path);duplicate_outputs+=1
                if run_ids is None and opened_below(directory):continue
                receipt=dict(run=run,publication=artifacts,owner_revision=revision,publication_run=authority_run,retirement_basis='settled' if settlement else 'published' if run in published else 'terminal-unpublished-inputs',retained_reference_files=len(retained),duplicate_outputs=duplicate_outputs,files=0,allocated=0,at=now.isoformat(),complete=False)
                if dry_run:
                    receipt.update(files=len(candidates),allocated=sum(getattr(p.stat(),'st_blocks',(p.stat().st_size+511)//512)*512 for p in candidates));report['runs'].append(receipt);continue
                # Serialize with Worker submission metadata, then take its file
                # store lock. No live request can race a terminal input exit.
                with closing(jobs.store.connect()) as db,db,(workspaces._lock if run_ids is None else nullcontext()):
                    db.execute('PRAGMA busy_timeout=0')
                    db.execute('BEGIN IMMEDIATE')
                    family=families.get(run,run)
                    current=family_jobs(db,family)
                    if any(status in {'queued','running'} for status,*_ in current):continue
                    if run_ids is None and (held(run) or held(family)):continue
                    fenced={run,family,*(r[1] for r in current)}
                    db.executemany('INSERT OR REPLACE INTO metadata VALUES(?,?)',[('workspace_retirement:'+r,now.isoformat()) for r in fenced])
                atomic_json(marker,receipt)
                # Submission is fenced by the retirement metadata; unlink outside writer locks.
                trash=directory/'audit'/'retiring-inputs'
                if trash.resolve().is_relative_to(directory.resolve()) is False:raise ValueError('linked input exit queue')
                if detach_areas:trash.mkdir(parents=True,exist_ok=True)
                for area in detach_areas:
                    area.rename(trash/(hashlib.sha256(str(area.relative_to(directory)).encode()).hexdigest()+'.'+uuid4().hex))
                for path in candidates:
                    info=path.stat();path.unlink();receipt['files']+=1
                    if info.st_nlink<=1:receipt['allocated']+=getattr(info,'st_blocks',(info.st_size+511)//512)*512
                if trash.is_dir():
                    from doxagent.v2_read.detached_files import unlink_page
                    entries=0;deadline=time.monotonic()+.05
                    with os.scandir(trash) as discarded:
                        for item in discarded:
                            if entries>=256 or time.monotonic()>=deadline:break
                            if item.is_symlink() or not item.is_dir(follow_symlinks=False):raise ValueError('invalid detached input')
                            done,removed,allocated=unlink_page(Path(item.path),max_entries=256-entries,deadline=deadline)
                            entries+=removed;receipt['allocated']+=allocated
                    receipt['detached_entries']=entries
                    with os.scandir(trash) as discarded:more=more or next(discarded,None) is not None
                if settlement:
                    from doxagent.workflows.codex_document3.state_v21 import StateV21
                    receipt['d3']=StateV21(source).retire(authority_run,now=now,authority='published-candidate')
                receipt['complete']=not more;atomic_json(marker,receipt)
                report['runs'].append(receipt)
            except (OSError,ValueError,sqlite3.Error) as exc:report['errors'].append(dict(run=run,error=str(exc)[:250]))
        if not dry_run and run_ids is None:
            # If the output limit stopped the page, revisit it; completed runs are cheap skips.
            next_cursor=cursor if len(report['runs'])>=limit else page[-1][0] if page else 0
            with closing(jobs.store.connect()) as db,db:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('workspace_exit_cursor',?)",(str(next_cursor),))
    return report
