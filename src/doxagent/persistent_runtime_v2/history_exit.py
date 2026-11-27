"""Existing Maintenance owner drives local histories; O3 execution is unrelated."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def collect(maintenance):
    from .resource_refs import backfill,pins
    from doxagent.event_library.repository import EventLibraryRepository
    from doxagent.event_library.admission_evidence import backfill as admissions
    from doxagent.event_library.lifecycle import register_legacy,collect_history,collect_batches
    from doxagent.codex_worker.session_retention import run_held
    journal,settings=maintenance.journal,maintenance.settings
    if not backfill(journal):return {'next_seconds':60,'phase':'live_reference_migration'}
    with maintenance.control._connection() as db:
        rows=db.execute("SELECT r.ticker,json_extract(r.payload,'$.artifacts.event_library'),"
            "json_extract(r.payload,'$.artifacts.document3.version') FROM activation_revisions r "
            'JOIN ticker_active_revision a USING(revision_id) ORDER BY r.ticker').fetchall()
    if not rows:return {'next_seconds':900}
    offset=getattr(maintenance,'_history_exit_offset',0)%len(rows)
    maintenance._history_exit_offset=offset+1
    ticker,event_json,policy_version=rows[offset]
    if not event_json:return {'next_seconds':900,'ticker':ticker,'deferred':'missing_event_reference'}
    event=json.loads(event_json);root=event.get('root') or settings.event_library_root
    path=Path(root)/'US'/ticker/'event_library.sqlite3'
    cached=getattr(maintenance,'_event_exit_repositories',{})
    if ticker not in cached or cached[ticker].path!=path:
        cached[ticker]=EventLibraryRepository(path)
        maintenance._event_exit_repositories=cached
    repository=cached[ticker]
    if not admissions(repository,ticker):return {'next_seconds':60,'ticker':ticker,'phase':'first_knowledge_migration'}
    seeded=register_legacy(repository,ticker)
    keep=pins(journal,ticker,event_root=str(root))|{int(event['version'])}
    from .resource_refs import daily_pins
    keep.update(daily_pins(journal, ticker, str(root)))
    if Path(root)==Path(settings.event_library_root):keep.update(daily_pins(journal,ticker,None))
    with journal.transaction(write=False,expand=False) as db:
        normal=db.execute("SELECT json_extract(receipt,'$._gc_owner'),json_extract(receipt,'$.o2_freeze.base'),status "
            "FROM runtime_tasks WHERE ticker=? AND kind='MAINTENANCE' "
            "AND status IN ('RUNNING','PENDING','HELD','FAILED') "
            "AND json_extract(receipt,'$.maintenance_retired') IS NULL "
            "AND coalesce(json_extract(inputs,'$.scope'),'')!='SUPPLEMENTAL' "
            "AND (json_extract(receipt,'$._gc_owner.event_library_root')=? OR json_extract(receipt,'$._gc_owner.event_library_root') IS NULL)", (ticker,str(root))).fetchall()
    def is_live(run):
        if not run:return True
        if run_held(settings,run):return True
        jobs=Path(settings.codex_workspace_root)/'worker-jobs.sqlite3'
        with closing(sqlite3.connect(jobs.resolve().as_uri()+'?mode=ro',uri=True,timeout=.01)) as db:
            return bool(db.execute("SELECT 1 FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id')>=? AND json_extract(payload,'$.run_id')<? AND status IN ('queued','running') LIMIT 1",(run,run+'\uffff')).fetchone())
    for raw,freeze,status in normal:
        owner=json.loads(raw) if raw else {}
        frozen=json.loads(freeze) if freeze else {}
        if status=='FAILED' and not is_live(owner.get('run_id')):continue
        owner_root=owner.get('event_library_root') or frozen.get('root') or settings.event_library_root
        if Path(owner_root)!=Path(root):continue
        keep.update(int(v) for v in owner.get('event_versions',[]))
        for version in (owner.get('event_library_version'),frozen.get('version')):
            if version is not None:keep.add(int(version))
    if Path(root)==Path(settings.event_library_root):
        with journal.transaction(write=False,expand=False) as db:
            keep.update(r[0] for r in db.execute('SELECT event_version FROM runtime_case_resource_refs WHERE event_root IS NULL AND ticker=?',(ticker,)) if r[0] is not None)
    # EventLibraryRef in the current typed D3 is a small stable header. Cache by
    # exact release, never read a large old PolicySet on every maintenance pass.
    if policy_version is not None:
        release=(ticker,policy_version)
        headers=getattr(maintenance,'_history_policy_event_headers',{})
        if release not in headers:
            with closing(sqlite3.connect(Path(settings.codex_runtime_sqlite_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute("SELECT json_extract(policy_set_json,'$.event_library_ref.version') FROM codex_document3_policy_sets WHERE ticker=? AND policy_set_version=?",release).fetchone()
            headers={key:value for key,value in headers.items() if key[0]!=ticker}
            headers[release]=row[0] if row else None
            maintenance._history_policy_event_headers=headers
        if headers[release] is not None:keep.add(int(headers[release]))
    with journal.transaction(write=False, expand=False) as db:
        sweeps=db.execute("SELECT json_extract(v.payload,'$.event_body_ref.version') FROM runtime_tasks t "
            "JOIN runtime_values v ON v.namespace='sweep_bundle' AND v.key=t.id "
            "WHERE t.ticker=? AND t.kind='SWEEP' AND t.status IN ('PENDING','RUNNING','HELD') "
            "AND (json_extract(v.payload,'$.event_body_ref.root')=? "
            "OR (json_extract(v.payload,'$.event_body_ref.root') IS NULL AND ?=1))",
            (ticker,str(root),int(Path(root)==Path(settings.event_library_root)))).fetchall()
    keep.update(r[0] for r in sweeps if r[0] is not None)
    from .resource_refs import legacy_effect_refs
    with journal.transaction(write=False,expand=False) as db:
        for _,legacy_root,version,_ in legacy_effect_refs(db,ticker):
            if version is not None and Path(legacy_root or settings.event_library_root)==Path(root):keep.add(version)
    batches=collect_batches(repository,now=journal.clock(),is_live=is_live,limit=2)
    history=collect_history(repository,ticker,keep_versions=keep,now=journal.clock())
    return {'ticker':ticker,'batches':batches,'history':history,
            'next_seconds':60 if not seeded or history.get('pending') or len(batches)==2 else 900}
