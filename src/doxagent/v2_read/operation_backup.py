"""Operation-owned SQLite snapshots; external Native/profile files are not archived here."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from .maintenance import atomic_json
from doxagent.trade_execution.worker import WriterLock


def snapshot(source,root,*,family,operation):
    source=Path(source).resolve(strict=True);root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256((str(source)+'\n'+family+'\n'+operation).encode()).hexdigest()[:24]
    marker=root/('operation-'+identity+'.json');target=root/('operation-'+identity+'.sqlite3')
    with WriterLock(root/('operation-'+identity)):
        if marker.is_file() and target.is_file():
            receipt=json.loads(marker.read_text())
            if receipt['source']!=str(source):raise ValueError('backup owner differs')
            return target
        pending=target.with_suffix('.pending')
        try:
            pending.unlink(missing_ok=True)
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(pending)) as dst:
                src.backup(dst,pages=256,sleep=.05)
                if not dst.execute('PRAGMA page_count').fetchone()[0]:raise ValueError('empty backup')
                dst.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
            pending.chmod(0o600);os.replace(pending,target)
            atomic_json(marker,dict(owner='doxagent.operation-backup.1',source=str(source),family=family,
                operation_sha256=hashlib.sha256(operation.encode()).hexdigest(),file=target.name,at=datetime.now(UTC).isoformat(),state='PENDING'))
        finally:pending.unlink(missing_ok=True)
    return target


def finish(target,*,success):
    target=Path(target).resolve(strict=True);marker=target.with_suffix('.json')
    receipt=json.loads(marker.read_text())
    receipt.update(state='COMPLETE' if success else 'FAILED',finished_at=datetime.now(UTC).isoformat())
    atomic_json(marker,receipt)
    return reconcile(target.parent)


def reconcile(root,*,now=None):
    root=Path(root).resolve();now=now or datetime.now(UTC);groups={};retired=[]
    if not root.is_dir():return retired
    for marker in root.glob('operation-*.json'):
        if marker.is_symlink():continue
        value=json.loads(marker.read_text())
        if value.get('owner')!='doxagent.operation-backup.1':continue
        target=root/value['file']
        if target.parent!=root or target.is_symlink():raise ValueError('backup escaped owner')
        groups.setdefault((value['source'],value['family']),[]).append((marker,target,value))
    for entries in groups.values():
        good=sorted((x for x in entries if x[2]['state']=='COMPLETE' and x[1].is_file()),key=lambda x:x[2]['at'])
        if not good:continue
        newest=good[-1]
        with closing(sqlite3.connect(newest[1].as_uri()+'?mode=ro',uri=True)) as db:
            if not db.execute('PRAGMA page_count').fetchone()[0]:continue
            db.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
        for marker,target,value in entries:
            if marker==newest[0] or value['at']>=newest[2]['at']:continue
            # Failed/stale operations retain their short recovery window.
            if value['state']!='COMPLETE' and now-datetime.fromisoformat(value['at'])<timedelta(days=3):continue
            target.unlink(missing_ok=True);marker.unlink();retired.append(str(target))
    return retired
