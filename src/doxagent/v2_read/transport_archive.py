"""Bounded source transport maintenance; old acknowledged packets truly expire."""
import argparse
import functools
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .content_files import ContentFiles


@functools.lru_cache(maxsize=4)
def load_pack(root, digest, size):
    if not 0 < size <= 16 * 1024 * 1024:
        raise ValueError('invalid transport pack size')
    files = ContentFiles(Path(root))
    raw = b''.join(files.read(digest, offset, min(files.CHUNK, size - offset))
                   for offset in range(0, size, files.CHUNK))
    return json.loads(raw)


def has_packs(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_receipt_packs'").fetchone() is not None


def archive(source, checkpoint, *, before=None, limit=128):
    before = before or (datetime.now(UTC) - timedelta(days=7)).isoformat()
    if datetime.fromisoformat(before) > datetime.now(UTC) - timedelta(days=7):
        raise ValueError('at least seven days of online transport required')
    if not 1 <= limit <= 500:
        raise ValueError('archive batch must be 1..500')
    with sqlite3.connect(Path(checkpoint).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        state = db.execute('SELECT epoch,head FROM v2_source_state WHERE source=?', (source.source,)).fetchone()
        if state is None:
            raise ValueError('checkpoint has no source identity')
        # A derived checkpoint retains the verified full offhost backup locator.
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoint_proof'").fetchone():
            proof = db.execute('SELECT archive,archive_sha256,source_sha256 FROM checkpoint_proof').fetchone()
            if proof is None or not proof[0] or any(len(x) != 64 for x in proof[1:]):
                raise ValueError('invalid offhost checkpoint proof')
    files = ContentFiles(source.path.parent / 'receipt-packs' / source.source)
    with source.connection(write=True) as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT epoch FROM v2_source_state WHERE source=?', (source.source,)).fetchone()
        if current is None or current[0] != state[0]:
            raise ValueError('checkpoint epoch mismatch')
        acknowledged = db.execute('SELECT min(checkpoint) FROM v2_source_consumers').fetchone()[0]
        if acknowledged is None:
            raise ValueError('no registered consumers')
        floor = min(acknowledged, state[1])
        pin = db.execute('SELECT min(seq) FROM v2_source_pins').fetchone()[0]
        if pin is not None:
            floor = min(floor, pin - 1)
        db.execute('CREATE TABLE IF NOT EXISTS v2_receipt_packs('
                   'seq INTEGER PRIMARY KEY,digest TEXT NOT NULL,size INTEGER NOT NULL,ordinal INTEGER NOT NULL)')
        # Scan a bounded prefix before applying age; timestamps may be unordered.
        candidates = db.execute('SELECT o.*,r.payload FROM v2_source_outbox o '
                                'JOIN v2_source_receipts r ON r.id=o.receipt_id '
                                'WHERE o.seq<=? ORDER BY o.seq LIMIT ?', (floor, limit)).fetchall()
        rows = [dict(r) for r in candidates if r['recorded_at'] < before]
        if not rows:
            return 0
        raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        while len(raw) > 16 * 1024 * 1024 and len(rows) > 1:
            rows = rows[:max(1, len(rows) // 2)]
            raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('single transport receipt exceeds pack size budget; retained online')
        identity = files.put(raw)  # Immutable compressed chunks fsynced before hot deletion.
        for ordinal, row in enumerate(rows):
            db.execute('INSERT INTO v2_receipt_packs VALUES(?,?,?,?)',
                       (row['seq'], identity, len(raw), ordinal))
            db.execute('DELETE FROM v2_source_outbox WHERE seq=?', (row['seq'],))
            db.execute('DELETE FROM v2_source_receipts WHERE id=?', (row['receipt_id'],))
        retained = db.execute('SELECT coalesce(min(seq)-1,?) FROM v2_source_outbox', (floor,)).fetchone()[0]
        db.execute('UPDATE v2_source_state SET retained_floor=max(retained_floor,?) WHERE source=?',
                   (retained, source.source))
    return len(rows)


def main():
    from .outbox import SourceOutbox
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', action='append', default=[])
    parser.add_argument('--checkpoint-root', type=Path, default=Path('/data/storage-archive/checkpoints'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    sources = [SourceOutbox(path, name) for name, path in (item.split('=', 1) for item in args.source)]
    for source in sources:
        if source.capture() is None:raise ValueError('source transport requires explicit migration: '+source.source)
    capacity_at=0
    while True:
        from doxagent.message_bus_v2.pressure import background_allowed
        if not args.once and not background_allowed():
            print(json.dumps({'maintenance':'transport_expiry','deferred':'MESSAGE_PIPELINE_RESOURCE_RESERVE'}),flush=True)
            time.sleep(15)
            continue
        started = time.monotonic()
        result = {}
        for source in sources:
            source_started = time.monotonic()
            count = 0
            scanned = 0
            error = None
            while time.monotonic() - source_started < 2 / max(1, len(sources)):
                try:
                    from .source_lifecycle import expire
                    batch = expire(source,limit=64)
                    changed = batch['expired']
                    scanned += batch['scanned']
                except (sqlite3.OperationalError, OSError, ValueError) as exc:
                    error = str(exc)
                    break
                count += changed
                if not changed:
                    break
            result[source.source] = dict(expired=count,scanned=scanned,error=error)
            if source.source=='bus' and time.monotonic()>=capacity_at:
                from .storage_status import capacity
                try:result[source.source]['capacity']=capacity(source)
                except (sqlite3.Error,OSError,ValueError) as exc:result[source.source]['capacity_error']=str(exc)[:200]
                capacity_at=time.monotonic()+3600
            try:
                from . import source_native_gc
                refs=source_native_gc.reconcile(source)
                result[source.source]['native_refs']=refs
                if refs['ready']:
                    result[source.source]['native_inventory']=source_native_gc.inventory(source)
                    result[source.source]['native_exit']=source_native_gc.collect(source)
            except (sqlite3.OperationalError,OSError,ValueError) as exc:
                result[source.source]['native_error']=str(exc)
        print(json.dumps(dict(maintenance='transport_expiry', sources=result,
                              elapsed_seconds=time.monotonic() - started)), flush=True)
        if args.once:
            break
        time.sleep(15)


if __name__ == '__main__':
    main()
