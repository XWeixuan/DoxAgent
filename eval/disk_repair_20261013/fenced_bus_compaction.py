"""One-shot server operation. All handles close before replacing a SQLite inode."""
import hashlib
import json
from contextlib import closing
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
SOURCE = ROOT / 'bus/bus.sqlite3'
SHADOW = SOURCE.with_name('bus.disk-repair-20261013.shadow.sqlite3')
RETIRED = SOURCE.with_name('bus.disk-repair-20261013.precompact.sqlite3')
REPORT = Path('/home/ubuntu/disk-repair-20261013/bus-compaction.json')
SERVICES = ['v2-transport-maintenance', 'v2-message-bus', 'v2-content-enrichment', 'v2-scheduler', 'v2-projector']


def handles(path):
    found = []
    key = path.stat()
    for folder in Path('/proc').glob('[0-9]*/fd'):
        try:
            descriptors = list(folder.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for fd in descriptors:
            try:
                info = fd.stat()
                if (info.st_dev, info.st_ino) == (key.st_dev, key.st_ino):
                    found.append(str(fd))
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
    return found


def state(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {name: db.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
        source = db.execute('SELECT * FROM v2_source_state ORDER BY source').fetchall()
        identities = hashlib.sha256()
        for table, key in [('standard_messages','standard_message_id'), ('distribution_deliveries','delivery_id'), ('v2_source_outbox','seq'), ('v2_receipt_packs','seq')]:
            if table in tables:
                for row in db.execute('SELECT * FROM '+table+' ORDER BY '+key):
                    identities.update(json.dumps(row,ensure_ascii=False,separators=(',',':')).encode()+b'\n')
        return dict(counts=counts, source=source, business_transport_sha256=identities.hexdigest())


def main():
    if SHADOW.exists() or RETIRED.exists():
        raise ValueError('existing compaction files require inspection')
    retirement = ROOT/'storage-archive/realtime-rejects-20261012T193459-56169bea.jsonl.manifest.retirement.json'
    receipt = json.loads(retirement.read_text())
    assert receipt['deleted'] == 10888166 and receipt['changed_preserved'] == 0
    stopped = []
    report = dict(before_bytes=SOURCE.stat().st_size, retirement=receipt, phase='drain')
    try:
        for service in SERVICES:
            name = 'doxagent-v2-'+service+'-1'
            subprocess.run(['docker','stop','-t','60',name], check=True)
            stopped.append(name)
        with closing(sqlite3.connect(SOURCE)) as db:
            checkpoint = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            assert checkpoint[0] == 0, checkpoint
            needed = (db.execute('PRAGMA page_count').fetchone()[0]-db.execute('PRAGMA freelist_count').fetchone()[0])*db.execute('PRAGMA page_size').fetchone()[0]
        assert not handles(SOURCE), handles(SOURCE)
        assert shutil.disk_usage(SOURCE.parent).free > needed + 2*1024**3, 'insufficient shadow reserve'
        report.update(phase='copy', live_bytes_estimate=needed, before=state(SOURCE))
        REPORT.write_text(json.dumps(report,indent=2))
        print(json.dumps(dict(phase='vacuum',needed=needed)),flush=True)
        with closing(sqlite3.connect(SOURCE.as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('VACUUM INTO ?', (str(SHADOW),))
        with closing(sqlite3.connect(SHADOW)) as db:
            assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            db.execute('PRAGMA journal_mode=WAL').fetchall()
        with closing(sqlite3.connect(SHADOW)) as db:
            assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
        report['after'] = state(SHADOW)
        assert report['before'] == report['after'], 'logical state mismatch'
        assert not handles(SOURCE) and not handles(SHADOW)
        for suffix in ('-wal','-shm'):
            path=Path(str(SOURCE)+suffix)
            if path.exists():
                assert not handles(path)
                path.unlink()
        os.chown(SHADOW,SOURCE.stat().st_uid,SOURCE.stat().st_gid)
        os.chmod(SHADOW,SOURCE.stat().st_mode & 0o777)
        os.link(SOURCE, RETIRED)
        os.replace(SHADOW, SOURCE)
        descriptor = os.open(SOURCE.parent,os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
        report.update(phase='switched', after_bytes=SOURCE.stat().st_size, retired_path=str(RETIRED), verified=True)
        REPORT.write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)
    finally:
        for name in reversed(stopped):
            subprocess.run(['docker','start',name],check=True)


if __name__ == '__main__':
    main()
