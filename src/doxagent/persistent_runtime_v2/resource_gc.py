"""Runtime owns terminal branches and process payloads; W3 materialization is excluded."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from functools import lru_cache

from doxagent.trade_execution.worker import WriterLock
from doxagent.v2_read.maintenance import atomic_json
from doxagent.v2_read.native_content import NativeContent
from .retained_events import build


@lru_cache(maxsize=4096)
def frame_owner(path, raw):
    value=NativeContent(path).decode(json.loads(raw)) if raw else {}
    return dict(run_id=value.get('run_id'),base=dict(artifacts=(value.get('base') or {}).get('artifacts',{})))


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


def referenced(journal,control_path,*,now=None):
    now=now or journal.clock();refs={};protected=set();runs={}
    def add(value,ticker=None,active=False):
        for root,symbol,version in event_refs(value,ticker):
            refs.setdefault(root,{}).setdefault(symbol,set()).add(version)
            if active:protected.add(root)
    with closing(sqlite3.connect(Path(control_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=NativeContent(control_path).row
        active={r[0] for r in db.execute('SELECT revision_id FROM ticker_active_revision')}
        for revision,ticker,payload in db.execute('SELECT revision_id,ticker,payload FROM activation_revisions'):
            add(json.loads(payload),ticker,revision in active)
    with journal.transaction(write=False) as db:
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Runtime control can be newer than the initialization pointer.
        if 'v2_ticker_control' in names:
            for ticker,payload in db.execute('SELECT ticker,payload FROM v2_ticker_control'):
                value=json.loads(payload)
                revision=value.get('activation_id')
                if revision:
                    with closing(sqlite3.connect(Path(control_path).resolve().as_uri()+'?mode=ro',uri=True)) as c:
                        c.row_factory=NativeContent(control_path).row
                        row=c.execute('SELECT payload FROM activation_revisions WHERE revision_id=?',(revision,)).fetchone()
                        if row:add(json.loads(row[0]),ticker,True)
        if 'runtime_v2_cases' in names:
            for root,ticker,version,status in db.execute("SELECT json_extract(payload_json,'$.version_pin.event_library_root'),ticker,json_extract(payload_json,'$.version_pin.event_library_version'),status FROM runtime_v2_cases"):
                if root and version is not None:
                    refs.setdefault(root,{}).setdefault(ticker,set()).add(int(version))
                    if status not in {'COMPLETED','FAILED','UNAVAILABLE'}:protected.add(root)
        for task in db.execute("SELECT id,ticker,status,json_extract(receipt,'$.frame') AS frame_json,updated_at FROM runtime_tasks WHERE kind IN ('MAINTENANCE','DELIVERY','SELECTION')"):
            identity,ticker,status,raw,at=tuple(task);frame=frame_owner(str(journal.path),raw);run=frame.get('run_id')
            age=(now-datetime.fromisoformat(at)).total_seconds()
            active=status in {'PENDING','RUNNING','HELD'} or status=='FAILED' and age<7*86400
            # A completed frame is audit metadata. Published activations and
            # Case version pins retain its valuable native history explicitly.
            if active:
                add(frame,ticker,True)
                inputs=db.execute('SELECT inputs FROM runtime_tasks WHERE id=?',(identity,)).fetchone()[0]
                add(json.loads(inputs),ticker,True)
            if run:
                candidate=str(journal.path.resolve().parent/'runtime_artifacts'/run/'events')
                if active:protected.add(candidate)
                runs[run]=dict(task=identity,status=status,at=at,active=active)
        for key,payload in db.execute("SELECT key,payload FROM runtime_values WHERE namespace='reference_deliveries'"):
            value=json.loads(payload);add(value,active=value.get('status') not in {'SUCCEEDED','FAILED'})
        # w3_library_lineage is a logical identity alias, not a file dependency
        # (w3_context compares it as a string). Keep those small rows intact.
        # Live W3 stages can retain a Case whose general status already settled.
        if 'runtime_v2_w3_stages' in names and 'runtime_v2_cases' in names:
            for root, in db.execute("SELECT DISTINCT json_extract(c.payload_json,'$.version_pin.event_library_root') FROM runtime_v2_w3_stages s JOIN runtime_v2_cases c USING(case_id) WHERE s.status IN ('PENDING','RUNNING','RESULT_SAVED') AND s.accepted_at>?",((now-timedelta(days=7)).isoformat(),)):
                if root:protected.add(root)
    jobs=journal.path.resolve().parent.parent/'workspaces'/'worker-jobs.sqlite3'
    if jobs.is_file():
        with closing(sqlite3.connect(jobs.as_uri()+'?mode=ro',uri=True)) as db:
            active=[r[0] for r in db.execute("SELECT json_extract(payload,'$.run_id') FROM jobs WHERE status IN ('queued','running')")]
        artifacts=journal.path.resolve().parent/'runtime_artifacts'
        if artifacts.exists():
            for run in artifacts.iterdir():
                if any(value==run.name or (value or '').startswith(run.name+'-') for value in active):protected.add(str(run/'events'))
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


def compact_one(journal,control_path,identity,run,path,versions,now):
    ticker=path.parent.name;staging=path.with_suffix('.native-staging')
    with WriterLock(path.with_suffix('.retirement')):
        for suffix in ('','-wal','-shm','.native-retained.json'):Path(str(staging)+suffix).unlink(missing_ok=True)
        def fingerprint():
            stat=path.stat();wal=Path(str(path)+'-wal')
            w=wal.stat() if wal.exists() else None
            return (stat.st_size,stat.st_mtime_ns,(w.st_size,w.st_mtime_ns) if w and w.st_size else None)
        stat=fingerprint();proof=build(path,staging,ticker,versions)
        with journal.transaction() as db:
            newrefs,newprotected,_=referenced(journal,control_path,now=now)
            current=newrefs.get(identity,{}).get(ticker,set())|newrefs.get(identity,{}).get(None,set())
            if identity in newprotected or not (current-{0})<=set(proof['versions']) or opened_below(path) or fingerprint()!=stat:
                staging.unlink(missing_ok=True);Path(str(staging)+'.native-retained.json').unlink(missing_ok=True);return None
            before=path.stat();size=getattr(before,'st_blocks',(before.st_size+511)//512)*512
            with staging.open('rb+') as stream:os.fsync(stream.fileno())
            os.replace(staging,path)
            for suffix in ('-wal','-shm'):Path(str(path)+suffix).unlink(missing_ok=True)
            proof.update(target=str(path),retired_allocated=size,retained_allocated=getattr(path.stat(),'st_blocks',(path.stat().st_size+511)//512)*512,at=now.isoformat())
            atomic_json(str(path)+'.native-retained.json',proof);Path(str(staging)+'.native-retained.json').unlink(missing_ok=True)
            return proof


def compact_process(journal,runs,protected,now,*,limit=64):
    import hashlib
    changed=[]
    for run,info in runs.items():
        if info['status']!='SUCCEEDED' or (now-datetime.fromisoformat(info['at'])).total_seconds()<7*86400:continue
        if str(journal.path.resolve().parent/'runtime_artifacts'/run/'events') in protected:continue
        prefix='runtime-worker:'+run+':'
        with journal.transaction(write=False) as db:
            keys=[r[0] for r in db.execute("SELECT key FROM runtime_values WHERE namespace='worker_requests' AND key>=? AND key<?",(prefix,prefix+'\uffff'))]
        for key in keys:
            if len(changed)>=limit:return changed
            request=journal.get('worker_requests',key);receipt=journal.get('worker_receipts',key)
            if not request or request.get('retired_process') or request.get('task_phase') is not None:continue
            if not receipt or receipt.get('status')!='succeeded':continue
            compact={k:request[k] for k in ('run_id','attempt_id','idempotency_key','node','ticker','thread_id','workflow_version','model','model_provider','effort') if k in request}
            compact.update(retired_process=True,request_sha256=hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
            with journal.transaction() as db:
                task=db.execute('SELECT status FROM runtime_tasks WHERE id=?',(info['task'],)).fetchone()
                if not task or task[0]!='SUCCEEDED':continue
                payload=journal.content.encode(compact)
                db.execute("UPDATE runtime_values SET payload=? WHERE namespace='worker_requests' AND key=?",(payload,key))
                telemetry=receipt.get('telemetry')
                if isinstance(telemetry,dict):
                    telemetry['events']=[]
                    db.execute("UPDATE runtime_values SET payload=? WHERE namespace='worker_receipts' AND key=?",(journal.content.encode(receipt),key))
                changed.append(key)
    return changed


def collect(journal,control_path,*,now=None,limit=8,dry_run=False):
    now=now or journal.clock();root=journal.path.resolve().parent/'runtime_artifacts'
    report=dict(at=now.isoformat(),retired=[],compacted=[],skipped=[],available_before=shutil.disk_usage(root.parent).free)
    if not root.exists():return report
    with WriterLock(root/'resource-retirement'):
        refs,protected,runs=referenced(journal,control_path,now=now)
        for run in sorted(root.iterdir()):
            if len(report['retired'])+len(report['compacted'])>=limit:break
            if not run.is_dir() or run.is_symlink():continue
            events=run/'events';identity=str(events);info=runs.get(run.name)
            if identity in protected:continue
            # An old superseded frame may no longer be in current receipts. It
            # still gets the failed-run window, rather than an endless unknown hold.
            stamp=datetime.fromtimestamp(run.stat().st_mtime,UTC)
            if info:stamp=datetime.fromisoformat(info['at'])
            age=(now-stamp).total_seconds();failed=info is None or info['status']=='FAILED'
            if age<(7*86400 if failed else 48*3600):continue
            if opened_below(run):report['skipped'].append(dict(run=run.name,reason='open descriptor'));continue
            used=refs.get(identity,{})
            if not used:
                if dry_run:report['retired'].append(dict(run=run.name,allocated=allocated(run)));continue
                # Admission and task-frame changes are fenced by the Runtime
                # writer. Recheck current pointers before the actual exit.
                with journal.transaction() as db:
                    _refs,_protected,_runs=referenced(journal,control_path,now=now)
                    if identity in _refs or identity in _protected or opened_below(run):continue
                    size=allocated(run);shutil.rmtree(run)
                    receipt=dict(run=run.name,allocated=size,at=now.isoformat())
                    db.execute("INSERT OR REPLACE INTO runtime_values VALUES('resource_retired',?,?)",(run.name,json.dumps(receipt)))
                    report['retired'].append(receipt)
                continue
            for path in events.glob('US/*/event_library.sqlite3'):
                ticker=path.parent.name;versions=used.get(ticker) or used.get(None)
                if not versions:continue
                marker=Path(str(path)+'.native-retained.json')
                if marker.is_file():
                    previous=json.loads(marker.read_text())
                    if previous.get('verified') and (set(versions)-{0})<=set(previous.get('versions',[])):continue
                if dry_run:report['compacted'].append(dict(run=run.name,path=str(path),versions=sorted(versions)));continue
                try:
                    proof=compact_one(journal,control_path,identity,run,path,versions,now)
                    if proof:report['compacted'].append(proof)
                except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
                    report['skipped'].append(dict(run=run.name,path=str(path),reason=type(exc).__name__+':'+str(exc)[:250]))
        if not dry_run:report['process_requests_retired']=compact_process(journal,runs,protected,now)
        report['available_after']=shutil.disk_usage(root.parent).free
        atomic_json(root/'last-resource-exit.json',report)
    return report
