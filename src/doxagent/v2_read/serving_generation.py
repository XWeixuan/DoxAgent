"""Build a new serving generation from a fenced, consistent Read snapshot.

Source business writers remain online. Pause the old Projector/GC so a long
snapshot cannot accumulate an unbounded WAL. Resume/catch up the new generation
before alias cutover. A failed target is disposable; the source is never mutated.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing,contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .content_files import ContentFiles
from .maintenance import atomic_json
from .native_content import MARKER, NativeContent
from .repository import ReadStore, encode, instant
from .serving_fields import select
from .serving_schema import normalize_definition


# These have immutable business identities, rather than technical update seqs.
IMMUTABLE = {
    'policy_detail', 'event_detail', 'fact', 'reference_event', 'library',
    'activation_revision', 'policy_set', 'document_ref', 'artifact_lineage',
    'research_section', 'research_download', 'research_run', 'citation',
    'expectations_run', 'shell', 'unit', 'future_node',
}


def normalize_hit_buckets(db, head):
    """Legacy aliases carried activation dimensions; V4 has one Policy bucket."""
    db.execute("DELETE FROM metric_buckets_current WHERE metric='policy_hits'")
    for day in ('day', "'*'"):
        db.execute("INSERT INTO metric_buckets_current(metric,ticker,day,dimensions,value,valid_from,valid_to) "
                   "SELECT 'policy_hits',ticker,"+day+",json_object('business_key',json_extract(dimensions,'$.policy_id')),"
                   "CAST(sum(CAST(value AS INTEGER)) AS TEXT),?,NULL FROM contributions_current "
                   "WHERE metric='policy_ar_hits' GROUP BY ticker,"+day+",json_extract(dimensions,'$.policy_id')",(head,))


@contextmanager
def generation_target(target):
    """Own one fresh generation directory, including exception cleanup."""
    target=Path(target).resolve()
    if target.exists() or target.parent.exists():
        raise ValueError('new generation requires a fresh dedicated directory')
    target.parent.mkdir(parents=True)
    try:yield target
    except BaseException:
        # This directory did not exist before this build. Shared/live roots
        # were rejected above; no production alias is changed by a builder.
        shutil.rmtree(target.parent)
        raise


def build(source, target, *, reserve_bytes=256 * 1024**2):
    source=Path(source).resolve(strict=True)
    with generation_target(target) as target:
        return _build(source,target,reserve_bytes=reserve_bytes)


def _build(source,target,*,reserve_bytes):
    old_codec = NativeContent(source)
    store = ReadStore(target)
    store.migrate()
    report = dict(format='doxagent.read-serving-generation.4', source=str(source),
                  target=str(target), at=instant(datetime.now(UTC)), copied={},
                  omitted={}, insurance_orphans={}, immutable_orphans_preserved=0)
    linked = set()
    started = time.monotonic()

    def room():
        if shutil.disk_usage(target.parent).free < reserve_bytes:
            raise OSError('serving generation exhausted its staging reserve')

    def adopt(value):
        """Verify and link only reachable immutable chunks; never copy old roots."""
        if isinstance(value, dict):
            if MARKER in value:
                link('native-files', value[MARKER])
                return value
            return {k: adopt(v) for k, v in value.items()}
        if isinstance(value, list):
            return [adopt(v) for v in value]
        return value

    def link(folder, digest):
        key = (folder, digest)
        if key in linked:
            return
        original = ContentFiles(source.parent / folder)
        root = original._path(digest)
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        observed = hashlib.sha256()
        for offset in range(0, manifest['size'], ContentFiles.CHUNK):
            observed.update(original.read(digest, offset, ContentFiles.CHUNK))
        if observed.hexdigest() != digest:
            raise ValueError('generation content checksum mismatch: ' + digest)
        dest = ContentFiles(target.parent / folder)._path(digest)
        dest.mkdir(parents=True)
        for item in root.iterdir():
            if item.is_symlink() or not item.is_file():
                raise ValueError('unexpected content member')
            room()
            try:
                os.link(item, dest / item.name)
            except OSError:
                shutil.copyfile(item, dest / item.name)
        linked.add(key)

    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.row_factory = sqlite3.Row
        src.execute('PRAGMA query_only=ON')
        src.execute('BEGIN')
        dst.execute('PRAGMA journal_mode=OFF').fetchall()
        dst.execute('PRAGMA synchronous=OFF')
        dst.execute('PRAGMA cache_size=-16384')
        dst.execute('PRAGMA recursive_triggers=ON')
        head = int(src.execute("SELECT value FROM read_meta WHERE key='highwater'").fetchone()[0])
        report['public_seq'] = head
        report['positions'] = [dict(r) for r in src.execute('SELECT * FROM checkpoints')]
        report['source_epochs'] = [dict(r) for r in src.execute('SELECT * FROM source_epochs')]
        report['prior_generation'] = src.execute('SELECT id FROM generation').fetchone()[0]
        report['baseline_gaps'] = [list(r) for r in src.execute('SELECT source,event,reason FROM gaps ORDER BY source,event')]
        # V4 never carries obsolete protocol tokens or closed technical versions.
        for table in ('read_meta', 'source_epochs', 'checkpoints', 'entity_watermarks',
                      'contribution_watermarks', 'coverage', 'capture_proofs', 'source_health'):
            dst.execute('DELETE FROM '+table)
            cols = [r[1] for r in src.execute('PRAGMA table_info('+table+')')]
            insert = 'INSERT INTO '+table+'('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')'
            count = 0
            cur = src.execute('SELECT '+','.join(cols)+' FROM '+table)
            while batch := cur.fetchmany(1000):
                room()
                if table == 'entity_watermarks':
                    batch = [r for r in batch if r['kind'] != 'policy_hit_observation']
                elif table == 'contribution_watermarks':
                    batch = [r for r in batch if r['metric'] != 'policy_hits']
                dst.executemany(insert, [tuple(r) for r in batch])
                count += len(batch)
                dst.commit()
            report['copied'][table] = count
        dst.execute("INSERT OR REPLACE INTO read_meta VALUES('retained_floor',?)", (str(head),))
        # After protocol reset, source positions + entity tombstones own replay
        # idempotency. Keep exact unresolved receipt identities, not old poll IDs.
        for row in src.execute('SELECT c.* FROM commits c JOIN gaps g ON g.source=c.source AND g.event=c.source_event'):
            dst.execute('INSERT INTO commits VALUES(?,?,?,?)', tuple(row))
        dst.executemany('INSERT INTO gaps VALUES(?,?,?,?,?)',
                        [tuple(r) for r in src.execute('SELECT * FROM gaps')])
        dst.execute("INSERT OR REPLACE INTO commit_retention VALUES(?,?)", (head, instant(datetime.now(UTC))))
        dst.execute("DELETE FROM sqlite_sequence WHERE name='commits'")
        dst.execute("INSERT INTO sqlite_sequence VALUES('commits',?)", (head,))
        dst.commit()
        cols = [r[1] for r in src.execute('PRAGMA table_info(object_current)')]
        insert = 'INSERT INTO object_current('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')'
        hits = defaultdict(dict)
        hit_seq = defaultdict(int)
        counts, omitted = Counter(), Counter()
        expected = hashlib.sha256()

        def transform(row):
            value = json.loads(row['payload'])
            # Only these containers need inspection below a preexisting pointer.
            if row['kind'] == 'native:runtime_v2_cases':
                for field in ('source', 'frozen_inputs'):
                    if field in value:
                        value[field] = old_codec.decode(value[field])
            value = select(row['kind'], row['id'], value)
            if value is None:
                return None
            if row['kind'] in {'fact', 'event_detail', 'policy_detail'}:
                field = {'fact':'fact', 'event_detail':'event', 'policy_detail':'policy'}[row['kind']]
                if field in value:
                    value[field] = old_codec.decode(value[field])
                value = normalize_definition(dst, row['kind'], value)
            value = adopt(value)
            return {**dict(row), 'payload': encode(value), 'valid_to': None}

        for row in src.execute('SELECT * FROM object_current ORDER BY rowid'):
            if row['kind'] == 'policy_hit_observation':
                value = json.loads(row['payload'])
                if value['hit']:
                    key = (row['ticker'], row['parent'])
                    hits[key][value['policy_id']] = {k: value[k] for k in ('ar', 'occurred_at')}
                    hit_seq[key] = max(hit_seq[key], row['valid_from'])
                omitted[row['kind']] += 1
                continue
            value = transform(row)
            if value is None:
                omitted[row['kind']] += 1
                continue
            values = tuple(value[c] for c in cols)
            dst.execute(insert, values)
            expected.update((encode(values)+'\n').encode())
            counts[row['kind']] += 1
            if sum(counts.values()) % 1000 == 0:
                room(); dst.commit()
                if time.monotonic()-started > 15:
                    print(encode(dict(phase='current', copied=sum(counts.values()),
                                      elapsed=time.monotonic()-started)), flush=True)
                    started = time.monotonic()
        dst.commit()
        actual = hashlib.sha256()
        for row in dst.execute('SELECT '+','.join(cols)+' FROM object_current ORDER BY rowid'):
            actual.update((encode(tuple(row))+'\n').encode())
        if actual.digest() != expected.digest():
            raise ValueError('current row transfer mismatch')
        report['current_transfer_sha256'] = actual.hexdigest()
        for (ticker, case), positive in hits.items():
            dst.execute('INSERT INTO object_current(kind,ticker,id,parent,sort_key,payload,valid_from) VALUES(?,?,?,?,?,?,?)',
                        ('policy_hit_set',ticker,case,case,case,encode({'hits':positive}),hit_seq[ticker,case]))
        counts['policy_hit_set'] = len(hits)
        # Only unique immutable identities absent from the serving set are
        # retained. Mutable native/process states are source responsibility.
        if src.execute("SELECT 1 FROM sqlite_master WHERE name='business_revisions'").fetchone():
            orphan_query = ('SELECT b.* FROM business_revisions b WHERE NOT EXISTS('
                            'SELECT 1 FROM objects o WHERE o.kind=b.kind AND o.ticker=b.ticker '
                            'AND o.id=b.id AND o.valid_from=b.valid_from AND o.payload=b.payload)')
            orphans = Counter()
            for row in src.execute(orphan_query):
                orphans[row['kind']] += 1
                if row['kind'] not in IMMUTABLE:
                    continue
                value = transform(row)
                present = dst.execute('SELECT payload FROM object_current WHERE kind=? AND ticker=? AND id=?',
                                      (row['kind'],row['ticker'],row['id'])).fetchone()
                if present:
                    if present[0] != value['payload']:
                        raise ValueError('immutable orphan conflicts with current business identity')
                else:
                    dst.execute(insert, tuple(value[c] for c in cols))
                    report['immutable_orphans_preserved'] += 1
            report['insurance_orphans'] = dict(orphans)
        dst.commit()
        # Persist actual positive facts once. Existing aggregate buckets preserve
        # exact Decimal and dimension semantics; their source remains the facts.
        for table in ('contributions_current','metric_buckets_current'):
            fields = [r[1] for r in dst.execute('PRAGMA table_info('+table+')')]
            cur = src.execute('SELECT '+','.join(fields)+' FROM '+table+" WHERE CAST(value AS NUMERIC)<>0"+
                              (" AND metric<>'policy_hits'" if table=='contributions_current' else ''))
            n = 0
            while batch := cur.fetchmany(1000):
                room()
                dst.executemany('INSERT INTO '+table+'('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',
                                [tuple(r) for r in batch])
                n += len(batch); dst.commit()
            report['copied'][table] = n
        normalize_hit_buckets(dst,head)
        for row in src.execute('SELECT * FROM contents'):
            dst.execute('INSERT INTO contents VALUES(?,?,?,?,?,?)', tuple(row))
            loc = src.execute('SELECT sha256 FROM content_locations WHERE id=?',(row['id'],)).fetchone()
            if loc:
                link('content-files',loc[0])
                dst.execute('INSERT INTO content_locations VALUES(?,?)',(row['id'],loc[0]))
                from .artifact_registry import retain
                retain(dst,row['id'],loc[0],row['size'],codec='utf8-chunks-v1',
                       location='content-files/'+loc[0][:2]+'/'+loc[0],owner_type='content',owner_id=row['id'])
        dst.commit()
        room()
        if dst.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('generation integrity check failed')
        if dst.execute('SELECT 1 FROM definition_ref r LEFT JOIN definitions d USING(sha256) WHERE d.sha256 IS NULL LIMIT 1').fetchone():
            raise ValueError('dangling business definition')
        report['copied']['object_current'] = dict(counts)
        report['omitted'] = dict(omitted)
        report['linked_content_objects'] = len(linked)
        report['generation'] = dst.execute('SELECT id FROM generation').fetchone()[0]
        dst.commit()
        dst.execute('PRAGMA journal_mode=WAL').fetchall()
        dst.execute('PRAGMA synchronous=FULL')
    with closing(sqlite3.connect(target)) as check:
        check.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
    store.migrate_policy_query_indexes()
    # Hardlinked content stays online when the old directory exits. It is not
    # counted as new unique data or double-counted as reclaimed old content.
    report.update(database_bytes=target.stat().st_size, verified=True)
    with target.open('rb+') as stream:
        os.fsync(stream.fileno())
    atomic_json(str(target)+'.serving.json',report)
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    print(encode(build(args.source,args.target)), flush=True)
