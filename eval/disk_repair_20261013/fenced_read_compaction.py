"""Keep API reads available while copying; close every handle for final replacement."""
import json
from contextlib import closing
import os
import sqlite3
import subprocess
from pathlib import Path

from fenced_bus_compaction import handles

ROOT = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/read')
SOURCE = ROOT/'v2.sqlite3'
SHADOW = ROOT/'v2.disk-repair-20261013.shadow.sqlite3'
RETIRED = ROOT/'v2.disk-repair-20261013.precompact.sqlite3'
WORK = Path('/home/ubuntu/disk-repair-20261013')


def coordinates(db):
    return dict(public_seq=db.execute('SELECT coalesce(max(seq),0) FROM commits').fetchone()[0],
                generation=db.execute('SELECT id FROM generation').fetchone()[0],
                positions=db.execute('SELECT * FROM checkpoints ORDER BY source').fetchall(),
                gaps=db.execute('SELECT * FROM gaps ORDER BY source,event').fetchall())


def main():
    assert not SHADOW.exists() and not RETIRED.exists()
    stopped=[]
    try:
        for service in ['v2-maintenance','v2-projector']:
            name='doxagent-v2-'+service+'-1'
            subprocess.run(['docker','stop','-t','60',name],check=True)
            stopped.append(name)
        with closing(sqlite3.connect(SOURCE)) as db:
            before=coordinates(db)
            assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]==0
        subprocess.run(['docker','run','--rm','--name','disk-repair-read-shadow-20261013',
                        '--network','none','--memory','3g','--memory-swap','4g','--cpus','1.5',
                        '-e','PYTHONUNBUFFERED=1','-v','doxagent-v2_v2-data:/data',
                        '-v',str(WORK/'shadow_compact.py')+':/app/src/doxagent/v2_read/shadow_compact.py:ro',
                        'doxagent-v2:disk-repair-read-20261013-r7','python','-m','doxagent.v2_read.shadow_compact',
                        '/data/read/v2.sqlite3','/data/read/'+SHADOW.name],check=True)
        # Only API token rows can have changed while projection was paused.
        name='doxagent-v2-v2-api-1'
        subprocess.run(['docker','stop','-t','60',name],check=True)
        stopped.append(name)
        with closing(sqlite3.connect(SOURCE)) as original, closing(sqlite3.connect(SHADOW)) as shadow:
            assert coordinates(original)==before, 'unexpected Read producer during shadow copy'
            assert coordinates(shadow)==before, 'shadow public/source coordinates changed'
            for table in ['views','cursors']:
                shadow.execute('DELETE FROM '+table)
                columns=len(original.execute('PRAGMA table_info('+table+')').fetchall())
                shadow.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in range(columns))+')',
                                   original.execute('SELECT * FROM '+table))
            shadow.commit()
            tokens={table:original.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['views','cursors']}
        for path in [SOURCE,SHADOW]:
            with closing(sqlite3.connect(path)) as db:
                assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]==0
        assert not handles(SOURCE) and not handles(SHADOW), 'unclosed Read handle'
        for suffix in ['-wal','-shm']:
            path=Path(str(SOURCE)+suffix)
            if path.exists():
                assert not handles(path)
                path.unlink()
        os.chown(SHADOW,SOURCE.stat().st_uid,SOURCE.stat().st_gid)
        os.chmod(SHADOW,SOURCE.stat().st_mode & 0o777)
        os.link(SOURCE,RETIRED)
        os.replace(SHADOW,SOURCE)
        descriptor=os.open(ROOT,os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
        report=dict(verified=True,phase='switched',coordinates=before,tokens=tokens,
                    before_bytes=RETIRED.stat().st_size,after_bytes=SOURCE.stat().st_size,
                    retired_path=str(RETIRED),shadow_manifest=str(SHADOW)+'.manifest.json')
        (WORK/'read-compaction.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)
    finally:
        for name in reversed(stopped):
            subprocess.run(['docker','start',name],check=True)


if __name__=='__main__':
    main()
