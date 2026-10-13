"""Exact superseded repair copies. No backup; retain receipts and business originals."""
import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from fenced_bus_compaction import handles

DATA = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
WORK = Path('/home/ubuntu/disk-repair-20261013')
TARGETS = [
    DATA/'backups/event-maintenance-20261006/runtime.sqlite3',
    DATA/'backups/event-maintenance-20261006/control.sqlite3',
    DATA/'bus/backups/pre-monitoring-activation-20260924.sqlite3',
    DATA/'bus/repair-backups/raw_d1c63813861a403c9778278e0002e4eb-20260923T174428Z.sqlite3',
    Path('/home/ubuntu/doxagent-backups/runtime-trade-20260930/case-shadow.sqlite3'),
    Path('/home/ubuntu/doxagent-backups/runtime-trade-20260930/read-before-backfill.sqlite3.zst'),
    Path('/var/lib/doxagent/initialization-repair/backups/initialization-20260922T042943Z.sqlite3'),
    Path('/var/lib/doxagent/initialization-repair/backups/initialization-20260922T042844Z.sqlite3'),
    Path('/var/lib/doxagent/initialization-repair/backups/runtime-20260922T042943Z.sqlite3'),
]


def main(apply=False):
    ids = subprocess.check_output(['docker','ps','-aq'],text=True).split()
    containers = json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
    report = dict(at=datetime.now(UTC).isoformat(), apply=apply,
                  reason='Superseded completed repair copies; newer 20261012 recovery set verified on D',
                  before_free=shutil.disk_usage(DATA).free, files=[], deleted_bytes=0)
    for base in TARGETS:
        paths = [base] + ([Path(str(base)+s) for s in ('-wal','-shm','.projector.executor.lock')]
                          if base.suffix=='.sqlite3' else [])
        for path in paths:
            if not path.exists():
                continue
            assert not path.is_symlink() and path.resolve(strict=True)==path, path
            opened = handles(path)
            references = []
            relative = '/data/'+str(path.relative_to(DATA)) if path.is_relative_to(DATA) else str(path)
            for container in containers:
                config = container['Config']
                # Output only the matched name, never environment values/secrets.
                values = json.dumps([config.get('Cmd'),config.get('Entrypoint'),config.get('Env')])
                if relative in values or str(path) in values:
                    references.append(container['Name'])
                for mount in container.get('Mounts',[]):
                    source = Path(mount['Source'])
                    # A normal /data mount does not reference every obsolete copy.
                    if source==path or source==path.parent:
                        references.append(container['Name']+':explicit-mount')
            entry=dict(path=str(path),bytes=path.stat().st_size,handles=opened,references=references,deleted=False)
            report['files'].append(entry)
            assert not opened and not references, entry
            if path.name.endswith('-wal'):
                assert path.stat().st_size==0, 'nonempty old WAL needs inspection'
            if apply:
                path.unlink()
                entry['deleted']=True
                report['deleted_bytes']+=entry['bytes']
                (WORK/'obsolete-snapshot-retirement.json').write_text(json.dumps(report,indent=2))
    report['after_free']=shutil.disk_usage(DATA).free
    (WORK/('obsolete-snapshot-retirement.json' if apply else 'obsolete-snapshot-audit.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    main(parser.parse_args().apply)
