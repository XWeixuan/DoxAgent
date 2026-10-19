"""Stream a fenced Read database into a compact shadow on the same filesystem.

The caller pauses projector/GC during the copy and API token writers for final
cutover. This keeps exact public sequences, generation and source coordinates;
it does not rebuild by replay or reinterpret unresolved provenance gaps.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .maintenance import atomic_json
from .native_content import MARKER, NativeContent


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def signature(row):
    return json.dumps([{'blob': bytes(v).hex()} if isinstance(v, (bytes, bytearray)) else v
                       for v in row], ensure_ascii=False, separators=(',', ':')).encode() + b'\n'


def build(source, target):
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    if target.exists() or source.parent != target.parent:
        raise ValueError('new shadow must share the existing immutable content directory')
    reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    writer = sqlite3.connect(target)
    report = {'source': str(source), 'target': str(target), 'tables': {}, 'at': datetime.now(UTC).isoformat()}
    codec = NativeContent(target)
    try:
        reader.execute('BEGIN')
        report['public_seq'] = reader.execute('SELECT coalesce(max(seq),0) FROM commits').fetchone()[0]
        report['generation'] = reader.execute('SELECT id FROM generation').fetchone()[0]
        report['positions'] = reader.execute('SELECT * FROM checkpoints ORDER BY source').fetchall()
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat().replace('+00:00', 'Z')
        floor = reader.execute('SELECT min(seq) FROM commit_retention WHERE projected_at>=?', (cutoff,)).fetchone()[0]
        floor = floor if floor is not None else reader.execute('SELECT coalesce(max(seq),0) FROM commits').fetchone()[0]
        now = datetime.now(UTC).isoformat().replace('+00:00', 'Z')
        for query in ('SELECT min(seq) FROM views WHERE expires_at>?',
                      "SELECT min(CAST(json_extract(payload,'$.seq') AS INTEGER)-1) FROM cursors WHERE expires_at>?"):
            token = reader.execute(query, (now,)).fetchone()[0]
            if token is not None:
                floor = min(floor, max(0, token))
        bus_gap = reader.execute("SELECT 1 FROM gaps WHERE source='bus' LIMIT 1").fetchone() is not None
        report.update(retention_floor=floor, bus_gap=bus_gap)
        writer.execute('PRAGMA journal_mode=OFF')  # New, disposable offline file only.
        writer.execute('PRAGMA synchronous=OFF')
        writer.execute('PRAGMA cache_size=-16384')
        writer.execute('PRAGMA temp_store=FILE')
        schema = reader.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
        tables = [name for kind, name, sql in schema if kind == 'table' and not name.startswith('sqlite_')]
        for kind, name, sql in schema:
            if kind == 'table' and not name.startswith('sqlite_'):
                writer.execute(sql)
        for table in tables:
            columns = [r[1] for r in reader.execute('PRAGMA table_info(' + quoted(table) + ')')]
            payload = columns.index('payload') + 1 if table in ('objects', 'object_history', 'object_current', 'business_revisions') else None
            kind_index = columns.index('kind') + 1 if table in ('objects', 'object_history') else None
            valid_to = columns.index('valid_to') + 1 if table in ('objects', 'object_history') else None
            query = 'SELECT rowid,' + ','.join(map(quoted, columns)) + ' FROM ' + quoted(table) + ' ORDER BY rowid'
            insert = 'INSERT INTO ' + quoted(table) + '(rowid,' + ','.join(map(quoted, columns)) + ') VALUES(' + ','.join('?' for _ in range(len(columns)+1)) + ')'
            digest = hashlib.sha256()
            copied = omitted = converted = 0
            cursor = reader.execute(query)
            last = time.monotonic()
            while batch := cursor.fetchmany(128):
                if shutil.disk_usage(target.parent).free < 1024**3:
                    raise ValueError('shadow construction has less than 1 GiB reserve')
                rows = []
                for original in batch:
                    row = list(original)
                    if table == 'changes':
                        # The streaming reader obtains exact interval pre-images from
                        # objects; these columns are old duplicate
                        # copies, as in maintenance.compact().
                        row[columns.index('before_payload') + 1] = None
                        row[columns.index('after_payload') + 1] = None
                    if table in ('objects', 'object_history') and not bus_gap and row[kind_index] == 'native:poll_states' and row[valid_to] is not None and row[valid_to] < floor:
                        omitted += 1
                        continue
                    if payload and len(row[payload]) >= 16384 and MARKER not in row[payload]:
                        packed = codec.encode(json.loads(row[payload]), keep={'inputs','source','summary','consumed','effective','polling'})
                        converted += packed != row[payload]
                        row[payload] = packed
                    digest.update(signature(row))
                    rows.append(row)
                writer.executemany(insert, rows)
                copied += len(rows)
                if time.monotonic() - last > 15:
                    writer.commit()
                    print(json.dumps(dict(table=table,copied=copied,omitted=omitted,converted=converted)), flush=True)
                    last = time.monotonic()
            writer.commit()
            actual = hashlib.sha256()
            for row in writer.execute(query):
                actual.update(signature(row))
            if actual.hexdigest() != digest.hexdigest():
                raise ValueError('shadow table digest mismatch: ' + table)
            report['tables'][table] = dict(copied=copied, omitted=omitted, converted=converted, sha256=digest.hexdigest())
            print(json.dumps(dict(table=table,**report['tables'][table])), flush=True)
        # AUTOINCREMENT's highwater may exceed MAX(seq) after legitimate retention.
        if reader.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            writer.execute('DELETE FROM sqlite_sequence')
            writer.executemany('INSERT INTO sqlite_sequence VALUES(?,?)', reader.execute('SELECT * FROM sqlite_sequence'))
            writer.commit()
        for kind, name, sql in schema:
            if kind in ('index', 'trigger', 'view'):
                if shutil.disk_usage(target.parent).free < 1024**3:
                    raise ValueError('shadow index construction has less than 1 GiB reserve')
                writer.execute(sql)
                writer.commit()
                print(json.dumps(dict(schema=kind,name=name)), flush=True)
        # Every copied table already has a full ordered digest and indexes were
        # freshly built by SQLite. Check page/B-tree integrity without repeating
        # millions of random index-to-table probes while projection is paused.
        if writer.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('shadow integrity check failed')
        writer.commit()
        writer.execute('PRAGMA journal_mode=WAL').fetchall()
        writer.close()
        writer = sqlite3.connect(target)
        writer.execute('PRAGMA synchronous=FULL')
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        report.update(source_bytes=source.stat().st_size, target_bytes=target.stat().st_size, verified=True)
        atomic_json(str(target) + '.manifest.json', report)
        with target.open('rb+') as stream:
            os.fsync(stream.fileno())
        return report
    finally:
        reader.close()
        writer.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.target)), flush=True)
