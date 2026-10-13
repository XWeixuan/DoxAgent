"""Lossless cold export of terminal realtime rejects; retire only after offhost ACK.

Run export/retire on the server. Pull and verify the immutable gzip on the offhost
machine, then upload its receipt as ACK. Articles, decisions, published deliveries,
sweeps and unfinished work are outside this program's retirement scope.
"""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(encode(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def export(database, folder, *, before, limit=None):
    if datetime.fromisoformat(before) > datetime.now(UTC) - timedelta(hours=24):
        raise ValueError('at least 24 hours of online terminal history required')
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    epoch = dict(db.execute("SELECT * FROM v2_source_state WHERE source='bus'").fetchone())
    upper = db.execute('SELECT max(rowid) FROM distribution_deliveries').fetchone()[0] or 0
    filename = 'realtime-rejects-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8] + '.jsonl.gz'
    path = folder / filename
    temporary = path.with_suffix('.partial')
    count = 0
    content = hashlib.sha256()
    # Force the rowid range, not a full state-index sort carrying eleven million
    # JSON payloads into a temporary B-tree on the production filesystem.
    query = ("SELECT d.rowid AS _archive_rowid,d.* FROM distribution_deliveries d NOT INDEXED "
             "WHERE d.rowid>? AND d.rowid<=? AND d.state='ADMISSION_REJECTED' "
             "AND d.created_at<? AND EXISTS(SELECT 1 FROM distribution_runs r "
             "WHERE r.run_id=d.run_id AND r.mode='REALTIME') ORDER BY d.rowid LIMIT ?")
    try:
        with temporary.open('xb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as stream:
                after = 0
                while True:
                    if shutil.disk_usage(folder).free < 1024**3:
                        raise ValueError('cold export has less than 1 GiB staging reserve')
                    size = min(1000, limit - count) if limit else 1000
                    if size <= 0:
                        break
                    rows = db.execute(query, (after, upper, before, size)).fetchall()
                    if not rows:
                        break
                    for row in rows:
                        line = (encode(dict(row)) + '\n').encode()
                        stream.write(line)
                        content.update(line)
                        count += 1
                    after = rows[-1]['_archive_rowid']
                    if count % 100000 == 0:
                        print(encode({'exported': count}), flush=True)
            raw.flush()
            os.fsync(raw.fileno())
        temporary.replace(path)
    finally:
        db.close()
    manifest = dict(format='doxagent.distribution-cold.1', database=str(database.resolve()),
                    epoch=epoch, before=before, upper_rowid=upper, file=filename,
                    rows=count, sha256=digest(path), bytes=path.stat().st_size,
                    content_sha256=content.hexdigest(), owner='message_bus_distribution',
                    protected=['articles', 'decisions', 'published', 'sweeps', 'unfinished'])
    atomic_json(path.with_suffix('.manifest.json'), manifest)
    return manifest


def retire(database, manifest_path, ack_path, *, batch=500):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    ack = json.loads(ack_path.read_text(encoding='utf-8'))
    if (ack.get('sha256') != manifest['sha256'] or not ack.get('verified')
            or ack.get('bytes') != manifest['bytes'] or not ack.get('offhost_path')):
        raise ValueError('matching verified offhost receipt required')
    path = manifest_path.parent / manifest['file']
    if path.resolve().parent != manifest_path.parent.resolve() or digest(path) != manifest['sha256']:
        raise ValueError('archive path or checksum mismatch')
    db = sqlite3.connect(database, timeout=.2)
    db.row_factory = sqlite3.Row
    epoch = db.execute("SELECT epoch FROM v2_source_state WHERE source='bus'").fetchone()[0]
    if epoch != manifest['epoch']['epoch'] or str(database.resolve()) != manifest['database']:
        raise ValueError('archive belongs to another source generation')
    deleted = preserved = visited = 0
    content = hashlib.sha256()
    # Verify the entire logical archive before the first destructive transaction.
    with gzip.open(path, 'rb') as stream:
        for line in stream:
            content.update(line)
            visited += 1
    if content.hexdigest() != manifest['content_sha256'] or visited != manifest['rows']:
        raise ValueError('logical archive verification failed')

    def apply(rows):
        nonlocal deleted, preserved
        identities = [r['_archive_rowid'] for r in rows]
        placeholders = ','.join('?' for _ in identities)
        while True:
            try:
                db.execute('BEGIN IMMEDIATE')
                current = {r['_archive_rowid']: dict(r) for r in db.execute(
                    'SELECT rowid AS _archive_rowid,* FROM distribution_deliveries NOT INDEXED '
                    'WHERE rowid IN (' + placeholders + ')', identities)}
                eligible = [r['_archive_rowid'] for r in rows if current.get(r['_archive_rowid']) == r]
                preserved += sum(identity in current and identity not in eligible for identity in identities)
                if eligible:
                    deleted += db.execute('DELETE FROM distribution_deliveries NOT INDEXED WHERE rowid IN ('
                                          + ','.join('?' for _ in eligible) + ')', eligible).rowcount
                db.commit()
                return
            except sqlite3.OperationalError as exc:
                db.rollback()
                if 'locked' not in str(exc):
                    raise
                time.sleep(.1)

    try:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            rows = []
            for line in stream:
                rows.append(json.loads(line))
                if len(rows) == batch:
                    apply(rows)
                    rows = []
                    if deleted % 100000 == 0:
                        print(encode({'deleted': deleted, 'changed_preserved': preserved}), flush=True)
                    db.execute('PRAGMA wal_checkpoint(PASSIVE)')
            if rows:
                apply(rows)
        report = dict(deleted=deleted, changed_preserved=preserved,
                      offhost_path=ack['offhost_path'], archive_sha256=manifest['sha256'])
        atomic_json(manifest_path.with_suffix('.retirement.json'), report)
        return report
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['export', 'retire'])
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--folder', type=Path)
    parser.add_argument('--before')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--ack', type=Path)
    args = parser.parse_args()
    if args.action == 'export':
        result = export(args.database, args.folder, before=args.before)
    else:
        result = retire(args.database, args.manifest, args.ack)
    print(encode(result), flush=True)
