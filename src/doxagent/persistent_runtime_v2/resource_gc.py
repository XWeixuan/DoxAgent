"""Runtime owns terminal branches and process payloads; W3 materialization is excluded."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

from doxagent.trade_execution.worker import WriterLock
from doxagent.v2_read.maintenance import atomic_json
from .retained_events import build

REFERENCE_OWNER_SQL="json_extract(payload,'$.event_library_root','$.root','$.ticker','$.status','$.event_library_version','$.version','$.before.version','$.after.version','$.before.\"$doxagent_content_v1\"','$.before.bytes','$.after.\"$doxagent_content_v1\"','$.after.bytes')"


TASK_RUN = "coalesce(json_extract(receipt,'$._gc_owner.run_id'),json_extract(receipt,'$.frame.run_id'))"
TASK_ROOT = "coalesce(json_extract(receipt,'$._gc_owner.event_library_root'),json_extract(receipt,'$.frame.base.artifacts.event_library.root'))"
TASK_VERSION = "coalesce(json_extract(receipt,'$._gc_owner.event_library_version'),json_extract(receipt,'$.frame.base.artifacts.event_library.version'))"
ACT_ROOT = "json_extract(payload,'$.artifacts.event_library.root')"


def migrate(journal, control_path=None):
    with journal.transaction(write=False) as db:
        names={r[0] for r in db.execute('SELECT name FROM sqlite_master')}
    required={'runtime_reference_owner','runtime_gc_task_run','runtime_gc_task_root'}
    if 'runtime_v2_cases' in names: required.add('runtime_case_library_owner')
    if not required <= names:
        with journal.transaction() as db:
            db.execute('CREATE INDEX IF NOT EXISTS runtime_reference_owner ON runtime_values('+REFERENCE_OWNER_SQL+") WHERE namespace='reference_deliveries'")
            if 'runtime_v2_cases' in names:
                db.execute("CREATE INDEX IF NOT EXISTS runtime_case_library_owner ON runtime_v2_cases(json_extract(payload_json,'$.version_pin.event_library_root'))")
            db.execute('CREATE INDEX IF NOT EXISTS runtime_gc_task_run ON runtime_tasks('+TASK_RUN+')')
            db.execute('CREATE INDEX IF NOT EXISTS runtime_gc_task_root ON runtime_tasks(kind,status,'+TASK_ROOT+')')
    if control_path:
        with closing(sqlite3.connect(control_path)) as db, db:
            db.execute('CREATE INDEX IF NOT EXISTS runtime_gc_activation_root ON activation_revisions('+ACT_ROOT+')')


def event_refs(value,ticker=None):
    if isinstance(value,dict):
        ticker=value.get('ticker',ticker)
        root=value.get('event_library_root') or value.get('root')
        version=value.get('event_library_version',value.get('version'))
        if isinstance(root,str) and Path(root).is_absolute() and (version is not None or root.replace('\\','/').rstrip('/').endswith('/events')):
            root=str(Path(root).resolve())
            versions=[]
            if isinstance(version,int):versions.append(version)
            for side in ('before','after'):
                v=(value.get(side) or {}).get('version') if isinstance(value.get(side),dict) else None
                if isinstance(v,int):versions.append(v)
            for v in versions:yield (root,ticker,v)
        for item in value.values():yield from event_refs(item,ticker)
    elif isinstance(value,list):
        for item in value:yield from event_refs(item,ticker)


def referenced(journal,control_path,*,now=None,only_root=None):
    if only_root is None:
        raise ValueError('resource references require one candidate root')
    now=now or journal.clock(); refs={}; protected=set(); runs={}
    def add(root,ticker,version):
        if root == only_root and isinstance(version,int):
            refs.setdefault(root,{}).setdefault(ticker,set()).add(version)
    # Small metadata only. Unknown native metadata retains the candidate.
    with closing(sqlite3.connect(Path(control_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        active={r[0] for r in db.execute('SELECT revision_id FROM ticker_active_revision')}
        with journal.transaction(write=False, expand=False) as runtime:
            if runtime.execute("SELECT 1 FROM sqlite_master WHERE name='v2_ticker_control'").fetchone():
                active.update(r[0] for r in runtime.execute("SELECT json_extract(payload,'$.activation_id') FROM v2_ticker_control"))
        rows=db.execute('SELECT revision_id,ticker,'+ACT_ROOT+",json_extract(payload,'$.artifacts.event_library.version'),json_extract(payload,'$.artifacts.\"$doxagent_content_v1\"'),json_extract(payload,'$.artifacts.event_library.\"$doxagent_content_v1\"') FROM activation_revisions WHERE "+ACT_ROOT+'=? OR '+ACT_ROOT+' IS NULL',(only_root,)).fetchall()
    for identity,ticker,root,version,opaque,opaque_event in rows:
        add(root,ticker,version)
        if opaque or opaque_event or root==only_root and identity in active:protected.add(only_root)
    with journal.transaction(write=False, expand=False) as db:
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cases=[]
        if 'runtime_v2_cases' in names:
            cases=db.execute("SELECT ticker,json_extract(payload_json,'$.version_pin.event_library_version'),status FROM runtime_v2_cases INDEXED BY runtime_case_library_owner WHERE json_extract(payload_json,'$.version_pin.event_library_root')=?",(only_root,)).fetchall()
        tasks=db.execute('SELECT id,ticker,status,'+TASK_RUN+','+TASK_ROOT+','+TASK_VERSION+",json_extract(receipt,'$.frame.\"$doxagent_content_v1\"'),updated_at FROM runtime_tasks WHERE kind IN ('MAINTENANCE','DELIVERY','SELECTION') AND status IN ('PENDING','RUNNING','HELD','FAILED') AND ("+TASK_ROOT+'=? OR '+TASK_ROOT+' IS NULL)',(only_root,)).fetchall()
        indexed=' INDEXED BY runtime_reference_owner'
        deliveries=[r[0] for r in db.execute('SELECT '+REFERENCE_OWNER_SQL+' AS owner_json FROM runtime_values'+indexed+" WHERE namespace='reference_deliveries'")]
        if 'runtime_v2_w3_stages' in names:
            retained=db.execute("SELECT 1 FROM runtime_v2_cases c INDEXED BY runtime_case_library_owner JOIN runtime_v2_w3_stages s USING(case_id) WHERE json_extract(c.payload_json,'$.version_pin.event_library_root')=? AND s.status IN ('PENDING','RUNNING','RESULT_SAVED') AND s.accepted_at>? LIMIT 1",(only_root,(now-timedelta(days=7)).isoformat())).fetchone()
            if retained:protected.add(only_root)
    for ticker,version,status in cases:
        add(only_root,ticker,version)
        if status not in {'COMPLETED','FAILED','UNAVAILABLE'}:protected.add(only_root)
    for identity,ticker,status,run,root,version,opaque,at in tasks:
        if status=='FAILED' and (now-datetime.fromisoformat(at)).total_seconds()>=7*86400:continue
        add(root,ticker,version)
        if root==only_root or opaque:protected.add(only_root)
        if run and str(journal.path.resolve().parent/'runtime_artifacts'/run/'events')==only_root:protected.add(only_root)
    for raw in deliveries:
        eroot,root,ticker,status,eversion,version,before,after,bhash,bsize,ahash,asize=json.loads(raw)
        root=eroot or root
        if root!=only_root:continue
        for v in (eversion if eversion is not None else version,before,after):add(root,ticker,v)
        if status not in {'SUCCEEDED','FAILED'} or before is None and bhash or after is None and ahash:protected.add(root)
    jobs=journal.path.resolve().parent.parent/'workspaces'/'worker-jobs.sqlite3'
    if jobs.is_file():
        with closing(sqlite3.connect(jobs.as_uri()+'?mode=ro',uri=True)) as db:
            active=[r[0] for r in db.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE status IN ('queued','running')")]
        run=Path(only_root).parent.name
        if any(value==run or (value or '').startswith(run+'-') for value in active):protected.add(only_root)
    return refs,protected,runs


def opened_below(path):
    if os.name!='posix':return False
    prefix=str(path)
    files=[path] if path.is_file() else [p for p in path.rglob('*') if p.is_file() and not p.is_symlink()]
    if path.is_file():files += [Path(str(path)+s) for s in ('-wal','-shm') if Path(str(path)+s).exists()]
    identities={(p.stat().st_dev,p.stat().st_ino) for p in files}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            cwd=os.readlink(proc/'cwd')
            if cwd==prefix or cwd.startswith(prefix+'/'):return True
        except OSError:pass
        for fd in (proc/'fd').glob('*'):
            try:
                value=os.readlink(fd)
                if value==prefix or value.startswith(prefix+'/') or value.startswith(prefix+'-'):return True
                stat=fd.stat()
                if (stat.st_dev,stat.st_ino) in identities:return True
            except OSError:pass
        try:
            for line in (proc/'maps').read_text().splitlines():
                fields=line.split(None,5);major,minor=fields[3].split(':')
                if (os.makedev(int(major,16),int(minor,16)),int(fields[4])) in identities:return True
        except (OSError,ValueError):pass
    return False


def allocated(path):
    total=0
    for parent,dirs,files in os.walk(path,followlinks=False):
        for name in files:
            s=(Path(parent)/name).lstat();total+=getattr(s,'st_blocks',(s.st_size+511)//512)*512
    return total


def compact_one(journal,control_path,identity,run,path,versions,now, should_yield=None):
    ticker=path.parent.name;staging=path.with_suffix('.native-staging')
    with WriterLock(path.with_suffix('.retirement')):
        for suffix in ('','-wal','-shm','.native-retained.json'):Path(str(staging)+suffix).unlink(missing_ok=True)
        def fingerprint():
            stat=path.stat();wal=Path(str(path)+'-wal')
            w=wal.stat() if wal.exists() else None
            return (stat.st_size,stat.st_mtime_ns,(w.st_size,w.st_mtime_ns) if w and w.st_size else None)
        stat=fingerprint();proof=build(path,staging,ticker,versions,should_yield=should_yield)
        # Detect any committed Runtime change while checking references. Heavy
        # decoding, /proc scanning and fsync must never own the business writer.
        with closing(sqlite3.connect(journal.path)) as watch:
            revision=watch.execute('PRAGMA data_version').fetchone()[0]
            newrefs,newprotected,_=referenced(journal,control_path,now=now,only_root=identity)
            current=newrefs.get(identity,{}).get(ticker,set())|newrefs.get(identity,{}).get(None,set())
            if identity in newprotected or not (current-{0})<=set(proof['versions']) or opened_below(path) or fingerprint()!=stat:
                staging.unlink(missing_ok=True);Path(str(staging)+'.native-retained.json').unlink(missing_ok=True);return None
            before=path.stat();size=getattr(before,'st_blocks',(before.st_size+511)//512)*512
            with staging.open('rb+') as stream:os.fsync(stream.fileno())
            with journal.transaction(operation='resource.compact.publish'):
                if watch.execute('PRAGMA data_version').fetchone()[0]!=revision or fingerprint()!=stat:
                    staging.unlink(missing_ok=True);Path(str(staging)+'.native-retained.json').unlink(missing_ok=True);return None
                wal=Path(str(path)+'-wal')
                if wal.exists() and wal.stat().st_size:
                    raise ValueError('immutable branch still has a committed WAL; checkpoint at its owner before retirement')
                os.replace(staging,path)
                for suffix in ('-wal','-shm'):Path(str(path)+suffix).unlink(missing_ok=True)
            proof.update(target=str(path),retired_allocated=size,retained_allocated=getattr(path.stat(),'st_blocks',(path.stat().st_size+511)//512)*512,at=now.isoformat())
            atomic_json(str(path)+'.native-retained.json',proof);Path(str(staging)+'.native-retained.json').unlink(missing_ok=True)
            return proof


def compact_process(journal,runs,protected,now,*,limit=64):
    import hashlib
    changed=[]
    for run,info in runs.items():
        if info['status'] not in {'SUCCEEDED','FAILED'}:continue
        days=1 if info['status']=='SUCCEEDED' else 14
        if (now-datetime.fromisoformat(info['at'])).total_seconds()<days*86400:continue
        if info['status']=='FAILED' and str(journal.path.resolve().parent/'runtime_artifacts'/run/'events') in protected:continue
        def terminal(db):
            task=db.execute('SELECT status,updated_at FROM runtime_tasks WHERE id=?',(info['task'],)).fetchone()
            return task and task[0]==info['status'] and (now-datetime.fromisoformat(task[1])).total_seconds()>=days*86400
        # This is only the pre-O3 retry input. The delivered before/after
        # evidence remains in reference_deliveries and native version pins.
        with journal.transaction() as db:
            if not terminal(db):continue
            db.execute("DELETE FROM runtime_values WHERE namespace='reference_baselines' AND key=?",(info['task'],))
        prefix='runtime-worker:'+run+':'
        with journal.transaction(write=False) as db:
            keys=[r[0] for r in db.execute("SELECT key FROM runtime_values WHERE namespace='worker_requests' AND key>=? AND key<?",(prefix,prefix+'\uffff'))]
        for key in keys:
            if len(changed)>=limit:return changed
            request=journal.get('worker_requests',key);receipt=journal.get('worker_receipts',key)
            if not request or request.get('retired_process') or request.get('task_phase') is not None or request.get('node')=='persistent_runtime_w3':continue
            if not receipt or receipt.get('status') not in {'succeeded','failed','cancelled'}:continue
            compact={k:request[k] for k in ('run_id','attempt_id','idempotency_key','node','ticker','thread_id','workflow_version','model','model_provider','effort') if k in request}
            compact.update(retired_process=True,request_sha256=hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
            payload=journal.content.encode(compact)
            telemetry=receipt.get('telemetry');encoded_receipt=None
            if isinstance(telemetry,dict):
                telemetry['events']=[]
                encoded_receipt=journal.content.encode(receipt)
            with journal.transaction(operation='resource.process.publish') as db:
                if not terminal(db):continue
                db.execute("UPDATE runtime_values SET payload=? WHERE namespace='worker_requests' AND key=?",(payload,key))
                if encoded_receipt is not None:
                    db.execute("UPDATE runtime_values SET payload=? WHERE namespace='worker_receipts' AND key=?",(encoded_receipt,key))
                changed.append(key)
    return changed


def collect(journal,control_path,*,now=None,limit=8,dry_run=False,should_yield=None):
    now=now or journal.clock();root=journal.path.resolve().parent/'runtime_artifacts'
    report=dict(at=now.isoformat(),retired=[],compacted=[],skipped=[],available_before=shutil.disk_usage(root.parent).free)
    if not root.exists():return report
    migrate(journal,control_path)
    previous=root/'last-resource-exit.json'
    try:prior=json.loads(previous.read_text());cursor=prior.get('cursor','')
    except (OSError,ValueError):prior={};cursor=''
    pending=list(dict.fromkeys(prior.get('pending',[])))
    paths=sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    retry=[p for p in paths if p.name in pending[:1]]
    paths=retry+[p for p in paths if p.name>cursor and p not in retry]
    deadline=time.monotonic()+5
    def pause():return time.monotonic()>=deadline
    with WriterLock(root/'resource-retirement'):
        for run in paths[:max(8,limit*4)]:
            if pause():break
            report['cursor']=run.name
            if run.name in pending:pending.remove(run.name)
            if run.name.startswith('.retiring-'):
                if not dry_run:shutil.rmtree(run)
                continue
            with journal.transaction(write=False,expand=False) as db:
                row=db.execute('SELECT id,status,updated_at FROM runtime_tasks WHERE '+TASK_RUN+'=? LIMIT 1',(run.name,)).fetchone()
            info=dict(task=row[0],status=row[1],at=row[2]) if row else None
            stamp=datetime.fromisoformat(info['at']) if info else datetime.fromtimestamp(run.stat().st_mtime,UTC)
            failed=info is None or info['status']=='FAILED'
            if info and info['status'] not in {'SUCCEEDED','FAILED'}:continue
            if info and info['status']=='SUCCEEDED' and (now-stamp).total_seconds()<48*3600:
                if not dry_run:
                    changed=compact_process(journal,{run.name:info},set(),now,limit=8)
                    report.setdefault('process_compacted',[]).extend(changed)
                continue
            if (now-stamp).total_seconds()<(7*86400 if failed else 48*3600):continue
            identity=str(run/'events')
            refs,protected,_=referenced(journal,control_path,now=now,only_root=identity)
            if pause():pending.append(run.name);break
            if info and not dry_run:
                changed=compact_process(journal,{run.name:info},protected,now,limit=8)
                report.setdefault('process_compacted',[]).extend(changed)
            if identity in protected:continue
            if opened_below(run):report['skipped'].append(dict(run=run.name,reason='open descriptor'));continue
            used=refs.get(identity,{})
            if not used:
                if dry_run:report['retired'].append(dict(run=run.name,allocated=allocated(run)));continue
                with closing(sqlite3.connect(journal.path)) as watch:
                    revision=watch.execute('PRAGMA data_version').fetchone()[0]
                    newrefs,newprotected,_=referenced(journal,control_path,now=now,only_root=identity)
                    if identity in newrefs or identity in newprotected or opened_below(run) or pause():continue
                    size=allocated(run);tombstone=run.with_name('.retiring-'+run.name)
                    receipt=dict(run=run.name,allocated=size,at=now.isoformat())
                    with journal.transaction(operation='resource.retire.publish') as db:
                        if watch.execute('PRAGMA data_version').fetchone()[0]!=revision:continue
                        os.replace(run,tombstone)
                        db.execute("INSERT OR REPLACE INTO runtime_values VALUES('resource_retired',?,?)",(run.name,json.dumps(receipt)))
                    shutil.rmtree(tombstone);report['retired'].append(receipt)
            else:
                for path in (run/'events').glob('US/*/event_library.sqlite3'):
                    versions=used.get(path.parent.name) or used.get(None)
                    if not versions:continue
                    marker=Path(str(path)+'.native-retained.json')
                    if marker.is_file():
                        prior=json.loads(marker.read_text())
                        if prior.get('verified') and (set(versions)-{0})<=set(prior.get('versions',[])):continue
                    if dry_run:report['compacted'].append(dict(run=run.name,path=str(path),versions=sorted(versions)));continue
                    try:
                        if should_yield and should_yield():
                            pending.append(run.name)
                            report['skipped'].append(dict(run=run.name,reason='active realtime; deferred heavy compaction'))
                            break
                        heavy_deadline=time.monotonic()+30
                        proof=compact_one(journal,control_path,identity,run,path,versions,now,
                                          lambda:time.monotonic()>=heavy_deadline)
                        if proof:report['compacted'].append(proof)
                    except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
                        pending.append(run.name)
                        report['skipped'].append(dict(run=run.name,reason=str(exc)[:200]))
            if len(report['retired'])+len(report['compacted'])>=limit:break
        if not paths or report.get('cursor')==paths[-1].name:report['cursor']=''
        report['pending']=list(dict.fromkeys(pending))
        report['available_after']=shutil.disk_usage(root.parent).free
        atomic_json(previous,report)
    return report
