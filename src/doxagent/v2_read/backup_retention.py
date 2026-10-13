"""Retire superseded successful migration copies at their owning producer."""
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing

from .maintenance import atomic_json

MARKER = '.completed-migration-backup.json'
OWNER = 'doxagent.production_v2.migrate'


def estimate_migration_backup(originals):
    total = 0
    roots = set()
    for path in originals.values():
        path = Path(path)
        if not path.exists():
            continue
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            total += db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
        roots.update(path.parent/name for name in ('content-files', 'native-files', 'published-contents',
                                                  'receipt-files', 'receipt-packs'))
    # Compressed immutable content is copied as-is. No decompression-size budget,
    # missing-WAL estimate, or unrelated workspaces/profile scan.
    total += sum(path.stat().st_size for root in roots for path in root.rglob('*')
                 if path.is_file() and not path.is_symlink())
    return total


def complete_migration_backup(directory, originals):
    directory = Path(directory).resolve(strict=True)
    sources = {name: str(Path(path).resolve()) for name, path in sorted(originals.items())}
    if any(Path(path).is_relative_to(directory.parent) for path in sources.values()):
        raise ValueError('migration backup parent must not contain business originals')
    receipt = dict(owner=OWNER, originals=sources, completed_at=datetime.now(UTC).isoformat())
    report = dict(retained=str(directory), retired=[], errors=[])
    # Successful snapshots no longer need the uncompressed resume representation.
    # Keep one compressed rollback set instead of leaving another full live-size DB.
    marker = directory / MARKER
    if not marker.exists():
        receipt['databases'] = []
        created_partial = None
        try:
            for source in directory.glob('*.sqlite3'):
                if source.is_symlink():
                    raise ValueError('migration backup must not contain linked databases')
                target = source.with_suffix(source.suffix+'.gz')
                partial = target.with_suffix(target.suffix+'.partial')
                original_hash = hashlib.sha256()
                with source.open('rb') as original, partial.open('xb') as raw:
                    created_partial = partial
                    with gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as compressed:
                        while chunk := original.read(1024*1024):
                            if shutil.disk_usage(directory).free < 1024**3:
                                raise OSError('migration compression has less than 1 GiB staging reserve')
                            original_hash.update(chunk)
                            compressed.write(chunk)
                    raw.flush()
                    os.fsync(raw.fileno())
                with gzip.open(partial, 'rb') as saved:
                    if hashlib.file_digest(saved, 'sha256').hexdigest() != original_hash.hexdigest():
                        raise ValueError('compressed migration snapshot checksum mismatch')
                if target.exists():
                    raise ValueError('compressed migration snapshot already exists')
                partial.replace(target)
                created_partial = None
                receipt['databases'].append(dict(file=target.name, original_bytes=source.stat().st_size,
                                                 sha256=original_hash.hexdigest()))
                source.unlink()
            atomic_json(marker, receipt)
        except (OSError, ValueError) as exc:
            if created_partial is not None:
                created_partial.unlink(missing_ok=True)
            report['errors'].append(dict(path=str(directory), error=str(exc)))
            return report  # Preserve previous rollback if new housekeeping did not finish.
    else:
        current = json.loads(marker.read_text(encoding='utf-8'))
        if current.get('owner') != OWNER or current.get('originals') != sources:
            raise ValueError('migration backup ownership differs')
    # Only this entry point's successful copies of the same database set. Failed,
    # explicit operator backups and unrelated directories have no matching marker.
    for candidate in sorted(directory.parent.iterdir()):
        if candidate == directory or candidate.is_symlink() or not candidate.is_dir():
            continue
        marker = candidate / MARKER
        if not marker.is_file() or marker.is_symlink():
            continue
        try:
            previous = json.loads(marker.read_text(encoding='utf-8'))
            if previous.get('owner') != OWNER or previous.get('originals') != sources:
                continue
            if candidate.resolve(strict=True).parent != directory.parent:
                raise ValueError('backup retirement escaped its owning parent')
            shutil.rmtree(candidate)
            report['retired'].append(str(candidate))
        except (OSError, ValueError) as exc:
            report['errors'].append(dict(path=str(candidate), error=str(exc)))
    return report
