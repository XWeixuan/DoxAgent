"""One-time exact-file recovery from the 2026-11-18 Event cold archives.

Pack locally from the audited plan; restore inside the Runtime owner container.
No Runtime tasks, Cases or trading receipts are modified or replayed.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tarfile


def pack(plan, archives, output):
    groups = defaultdict(list)
    entries = []
    for ticker in json.loads(plan.read_text(encoding='utf-8')):
        for item in ticker['required']:
            if item['exists']:
                continue
            receipt = item['archive_receipt']
            entry = dict(path=receipt['retired'], sha256=receipt['sha256'],
                         ticker=ticker['ticker'], ceiling=item['ceiling'])
            entries.append(entry)
            groups[receipt['archive']].append(entry)
    output.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = 0
    with output.open('xb') as target, tarfile.open(fileobj=target, mode='w:gz') as result:
        manifest = json.dumps(entries).encode()
        header = tarfile.TarInfo('manifest.json'); header.size = len(manifest)
        result.addfile(header, io.BytesIO(manifest))
        for archive, required in groups.items():
            with tarfile.open(archives/archive, 'r:gz') as source:
                for entry in required:
                    member = source.getmember(entry['path'])
                    if not member.isfile():
                        raise ValueError('not a regular file: '+entry['path'])
                    with source.extractfile(member) as stream:
                        content = stream.read()
                    if hashlib.sha256(content).hexdigest() != entry['sha256']:
                        raise ValueError('cold archive checksum mismatch: '+entry['path'])
                    header = tarfile.TarInfo(entry['path']); header.size = len(content)
                    result.addfile(header, io.BytesIO(content))
                    original_bytes += len(content)
            print(json.dumps(dict(archive=archive, packed=len(required))), flush=True)
    print(json.dumps(dict(files=len(entries), original_bytes=original_bytes,
                          pack_bytes=output.stat().st_size)), flush=True)


def restore(archive):
    from doxagent.trade_execution.worker import WriterLock
    root = Path('/data/runtime/runtime_artifacts').resolve(strict=True)
    pattern = r'runtime-maintain-[0-9a-f]{24}/events/US/[A-Z0-9._-]+/event_library\.sqlite3'
    restored = existing = 0
    with tarfile.open(archive, 'r:gz') as source:
        entries = json.load(source.extractfile('manifest.json'))
        if len({e['path'] for e in entries}) != len(entries):
            raise ValueError('duplicate recovery paths')
        with WriterLock(root/'resource-retirement'):
            for entry in entries:
                relative = entry['path']
                if not re.fullmatch(pattern, relative):
                    raise ValueError('invalid recovery path: '+relative)
                target = (root/relative).resolve()
                if not target.is_relative_to(root) or not target.parent.is_dir():
                    raise ValueError('recovery target left existing artifact tree')
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']:
                        raise ValueError('existing file differs: '+relative)
                    existing += 1
                    continue
                staging = target.with_suffix('.lineage-recovery')
                with source.extractfile(relative) as stream, staging.open('xb') as out:
                    digest = hashlib.sha256(); size = 0
                    while block := stream.read(1024*1024):
                        out.write(block); digest.update(block); size += len(block)
                    out.flush(); os.fsync(out.fileno())
                if digest.hexdigest() != entry['sha256'] or size != source.getmember(relative).size:
                    raise ValueError('recovery checksum mismatch: '+relative)
                with closing(sqlite3.connect(staging.as_uri()+'?mode=ro', uri=True)) as db:
                    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('SQLite integrity failure: '+relative)
                    if not db.execute("SELECT 1 FROM library_versions WHERE ticker=? AND version=? AND status='PUBLISHED'",
                                      (entry['ticker'], entry['ceiling'])).fetchone():
                        raise ValueError('required publication unavailable: '+relative)
                # Never replace a file created by another owner during recovery.
                os.link(staging, target)
                staging.unlink()
                restored += 1
    print(json.dumps(dict(restored=restored, already_present=existing)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    command = sub.add_parser('pack')
    command.add_argument('plan', type=Path)
    command.add_argument('archives', type=Path)
    command.add_argument('output', type=Path)
    command = sub.add_parser('restore')
    command.add_argument('archive', type=Path)
    args = parser.parse_args()
    if args.action == 'pack':
        pack(args.plan, args.archives, args.output)
    else:
        restore(args.archive)
