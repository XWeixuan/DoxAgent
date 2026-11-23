"""Bounded source-native reachability reconciliation, owned by transport maintenance."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import re
import shutil
import time

from .outbox import quoted
from .source_lifecycle import migrate as migrate_transport, native_hashes, replace_packet_hashes


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS v2_native_ref_scan(owner TEXT PRIMARY KEY,cursor INTEGER,done INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS v2_native_exit(id TEXT PRIMARY KEY,not_before REAL)')
    db.execute('CREATE INDEX IF NOT EXISTS v2_native_exit_due ON v2_native_exit(not_before,id)')
    db.execute('CREATE TABLE IF NOT EXISTS v2_native_inventory(id INTEGER PRIMARY KEY CHECK(id=1),after_digest TEXT)')


def queue_owner_exit(db,identities):
    """Explicit process-owner settlement; every other reference still protects content."""
    identities=list(identities)
    if not identities:return
    migrate(db)
    db.executemany("INSERT INTO v2_native_exit VALUES(?,-1) ON CONFLICT(id) DO UPDATE SET not_before="
        "CASE WHEN (SELECT state FROM artifact WHERE id=excluded.id)='LIVE' THEN -1 ELSE v2_native_exit.not_before END",[(x,) for x in identities])


def orphan_refs(db,table,owner,keys,*,limit,started,seconds,packet=False):
    """Page the existing owner index; look up bare primary keys, never N x N."""
    scan='orphan:'+owner
    cursor,done=db.execute('SELECT cursor,done FROM v2_native_ref_scan WHERE owner=?',(scan,)).fetchone()
    if done:return 0
    after=cursor[3:] if isinstance(cursor,str) and cursor.startswith('id:') else ''
    rows=db.execute('SELECT DISTINCT owner_id FROM artifact_ref WHERE owner_type=? AND owner_id>? ORDER BY owner_id LIMIT ?',(owner,after,limit)).fetchall()
    checked=0
    for identity, in rows:
        try:
            values=[int(identity)] if packet or not keys else json.loads(identity)
            if not isinstance(values,list) or len(values)!=max(1,len(keys)):raise ValueError('legacy owner identity')
            predicate=' AND '.join(quoted(k)+' IS ?' for k in keys) if keys else 'rowid IS ?'
            present=db.execute('SELECT 1 FROM '+quoted(table)+' WHERE '+predicate+' LIMIT 1',values).fetchone()
        except (ValueError,TypeError,OverflowError):present=None
        if not present:db.execute('DELETE FROM artifact_ref WHERE owner_type=? AND owner_id=?',(owner,identity))
        after=identity;checked+=1
        if time.monotonic()-started>=seconds:break
    # Prefix prevents SQLite's INTEGER affinity converting numeric owner IDs;
    # this phase's cursor is lexicographic, unlike the source-table row cursor.
    db.execute('UPDATE v2_native_ref_scan SET cursor=?,done=? WHERE owner=?',('id:'+after,int(not rows),scan))
    return checked


def reconcile(source, *, limit=64, seconds=.3):
    """Repair old append-only owner refs before considering any source CAS file."""
    started=time.monotonic();report=dict(rows=0,owners=0,ready=False)
    with closing(source.connection()) as db:
        version = db.execute("PRAGMA schema_version").fetchone()[0]
        cached = getattr(source, "_native_schema", None)
        if cached is None or cached[0] != version:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tables = []
            for table in sorted(names):
                if table.startswith("sqlite_"):
                    continue
                info = list(db.execute("PRAGMA table_info(" + quoted(table) + ")"))
                columns = [row[1] for row in info
                           if row[1] in {"payload", "payload_json", "inputs", "receipt", "data_json"}]
                if columns:
                    keys = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
                    tables.append((table, columns, keys))
            packets = [(table, owner) for table, owner in (
                ("v2_receipt_packs", "transport_pack"),
                ("v2_receipt_archive", "transport_archive"),
                ("v2_source_exception_receipts", "transport_exception"),
            ) if table in names]
            owners = [(table, table, columns, keys) for table, columns, keys in tables]
            owners += [(table, owner, [], ["seq"]) for table, owner in packets]
            source._native_schema = (version, names, owners)
        else:
            _, names, owners = cached
        wanted = {name for _, owner, _, _ in owners for name in (owner, "orphan:" + owner)}
        scan = dict((row[0], (row[1], row[2])) for row in db.execute(
            "SELECT owner,cursor,done FROM v2_native_ref_scan"
        )) if "v2_native_ref_scan" in names else {}
    if wanted - scan.keys():
        with closing(source.connection(write=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            migrate(db)
            db.executemany("INSERT OR IGNORE INTO v2_native_ref_scan VALUES(?,0,0)",
                           [(key,) for key in wanted - scan.keys()])
    for table, owner, columns, keys in owners:
        done = scan.get(owner, (0, 0))[1]
        orphan_done = scan.get("orphan:" + owner, (0, 0))[1]
        if done and orphan_done:
            continue
        if time.monotonic() - started >= seconds and report["rows"]:
            break
        selector = "rowid" if columns else "seq"
        fields = list(dict.fromkeys(keys + columns))
        selected = ",".join(map(quoted, fields))
        key_positions = [1 + fields.index(key) for key in keys] if columns else []
        payload_positions = [1 + fields.index(column) for column in columns]
        query = ("SELECT " + selector + "," + selected) if columns else "SELECT *"
        query += " FROM " + quoted(table)
        prepared = []
        if not done:
            cursor = scan.get(owner, (0, 0))[0]
            with closing(source.connection()) as db:
                rows = db.execute(query + f" WHERE {selector}>? ORDER BY {selector} LIMIT ?",
                                  (cursor, limit)).fetchall()
                for row in rows:
                    identity = json.dumps([row[position] for position in key_positions],
                                          separators=(",", ":"), ensure_ascii=False) if keys and columns else str(row[0])
                    raw = tuple(row) if columns else source.event(row[0], decode=False, connection=db)
                    if raw is None:
                        raise ValueError("native ref packet unavailable")
                    prepared.append((tuple(row), identity, set(native_hashes(raw if not columns else [raw[position] for position in payload_positions]))))
                    if time.monotonic() - started >= seconds:
                        break
        with closing(source.connection(write=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if done:
                checked = orphan_refs(db, table, owner, keys, limit=limit, started=started,
                                      seconds=seconds, packet=not columns)
                report["orphan_owners"] = report.get("orphan_owners", 0) + checked
                continue
            if tuple(db.execute("SELECT cursor,done FROM v2_native_ref_scan WHERE owner=?", (owner,)).fetchone()) != (cursor, 0):
                continue
            for row, identity, hashes in prepared:
                current = db.execute(query + f" WHERE {selector}=?", (row[0],)).fetchone()
                if current is None or tuple(current) != row:
                    break
                replace_packet_hashes(db, owner, identity, hashes)
                cursor = row[0]
                report["rows"] += 1
            finished = not rows and not db.execute(
                "SELECT 1 FROM " + quoted(table) + f" WHERE {selector}>? LIMIT 1", (cursor,)
            ).fetchone()
            report["owners"] += int(finished)
            db.execute("UPDATE v2_native_ref_scan SET cursor=?,done=? WHERE owner=?",
                       (cursor, int(finished), owner))
    with closing(source.connection()) as db:
        report["ready"] = not db.execute("SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1").fetchone()
    return report


def inventory(source, *, limit=64):
    """Register only verified-format native CAS paths, including abandoned puts."""
    from .content_files import ContentFiles, SINGLE_FILE

    root = source.path.parent / "native-files"
    with closing(source.connection()) as db:
        if db.execute("SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1").fetchone():
            return 0
        row = db.execute("SELECT after_digest FROM v2_native_inventory WHERE id=1").fetchone()
    after = row[0] if row else ""
    if root.is_symlink():
        raise ValueError("native root is linked")
    import os

    scan = getattr(source, "_native_inventory_scan", None)
    if scan is not None and scan[0] != after:
        scan[2].close()
        scan = None
    if scan is None:
        prefix = int(after[2:], 16) if after.startswith("p:") else (
            int(after[:2], 16) if after else 0
        )
        scan = (after, prefix, None)
    _, prefix, entries = scan
    prepared = []
    inspected = 0
    prefixes = 0
    while inspected < limit and prefixes < 256:
        if entries is None:
            folder = root / f"{prefix:02x}"
            if folder.is_symlink():
                raise ValueError("native prefix is linked")
            try:
                entries = os.scandir(folder)
            except FileNotFoundError:
                prefixes += 1
                prefix = (prefix + 1) % 256
                continue
        try:
            entry = next(entries)
        except StopIteration:
            entries.close()
            entries = None
            prefixes += 1
            prefix = (prefix + 1) % 256
            continue
        inspected += 1
        path = root / f"{prefix:02x}" / entry.name
        if entry.is_symlink() or not re.fullmatch("[0-9a-f]{64}", entry.name) or not entry.is_dir():
            continue
        if path.name[:2] != f"{prefix:02x}":
            raise ValueError("native CAS prefix mismatch")
        with closing(source.connection()) as db:
            known = db.execute(
                "SELECT 1 FROM artifact WHERE id=? AND sha256=? AND size>0 "
                "AND codec='json-chunks-v1' AND location=?",
                (path.name, path.name, f"native-files/{path.name[:2]}/{path.name}"),
            ).fetchone()
        if known:
            continue
        manifest = path / "manifest.json"
        single = path / SINGLE_FILE
        try:
            if single.is_file() and not single.is_symlink():
                size = ContentFiles(root).verify(path.name)
                created = datetime.fromtimestamp(single.stat().st_mtime, UTC).isoformat()
            else:
                value = json.loads(manifest.read_text(encoding="utf-8"))
                if value.get("sha256") != path.name or value.get("version") != 1:
                    raise ValueError("native inventory manifest mismatch")
                size = value['size']
                created = datetime.fromtimestamp(manifest.stat().st_mtime, UTC).isoformat()
        except FileNotFoundError:
            continue
        prepared.append((path.name, size, created))
    next_cursor = f"p:{prefix:02x}"
    source._native_inventory_scan = (next_cursor, prefix, entries)
    if not prepared and next_cursor == after:
        return 0
    with closing(source.connection(write=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT after_digest FROM v2_native_inventory WHERE id=1").fetchone()
        if (tuple(current) if current else None) != (tuple(row) if row else None) or db.execute("SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1").fetchone():
            return 0
        db.executemany(
            "INSERT OR IGNORE INTO artifact(id,sha256,size,codec,location,class,created_at) "
            "VALUES(?,?,?,'json-chunks-v1',?,'BUSINESS',?) ON CONFLICT(id) DO UPDATE SET size=excluded.size "
            "WHERE artifact.sha256=excluded.sha256 AND artifact.size=0 AND artifact.codec=excluded.codec "
            "AND artifact.location=excluded.location",
            [(digest, digest, size, f"native-files/{digest[:2]}/{digest}", created)
             for digest, size, created in prepared],
        )
        db.execute(
            "INSERT INTO v2_native_inventory VALUES(1,?) "
            "ON CONFLICT(id) DO UPDATE SET after_digest=excluded.after_digest "
            "WHERE after_digest<>excluded.after_digest",
            (next_cursor,),
        )
    return len(prepared)


def collect(source, *, now=None, limit=32, queued_only=False):
    """Current/immutable/transport/hold refs protect files; unowned puts get 7d."""
    from doxagent.trade_execution.worker import WriterLock

    # Serialize collectors for this file pool across the post-commit delete.
    # Publishers continue to use their ordinary SQLite writer and state guards.
    try:
        with WriterLock(source.path.parent / 'native-collection'):
            return _collect(source, now=now, limit=limit, queued_only=queued_only)
    except RuntimeError as exc:
        if str(exc) != 'EXECUTOR_ALREADY_RUNNING':
            raise
        return dict(quarantined=0, deleted=0, allocated=0, deferred='collector_active')


def _collect(source, *, now, limit, queued_only=False):
    from .detached_files import detach, drain

    now=now or datetime.now(UTC);report=dict(quarantined=0,deleted=0,allocated=0)
    protected="EXISTS(SELECT 1 FROM artifact_ref r WHERE r.artifact_id=a.id) OR EXISTS(SELECT 1 FROM retention_hold h WHERE h.released_at IS NULL AND (h.scope='ALL' OR h.id=a.id))"
    root=source.path.parent.resolve();quarantine=root/'native-quarantine'
    if quarantine.is_symlink():
        raise ValueError('linked Native quarantine')
    discard = quarantine / '.discarded'
    report['discarded'] = drain(discard,limit=1 if queued_only else 4)
    claimed = []
    cutoff = (now - timedelta(days=7)).isoformat()
    with closing(source.connection()) as db:
        if not db.execute("SELECT 1 FROM v2_native_ref_scan LIMIT 1").fetchone() or db.execute(
            "SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1"
        ).fetchone():
            return report
        cursor_row = db.execute(
            "SELECT cursor FROM v2_transport_gc_state WHERE kind='native-age-scan'"
        ).fetchone() if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='v2_transport_gc_state'"
        ).fetchone() else None
        cursor = cursor_row[0][3:] if cursor_row else ""
        page = [] if queued_only else db.execute(
            "SELECT id, state, codec, location, created_at FROM artifact "
            "WHERE id>? ORDER BY id LIMIT 256", (cursor,),
        ).fetchall()
        aged = []
        next_cursor = ""
        for inspected, (digest, state, codec, location, created) in enumerate(page, 1):
            next_cursor = digest
            if state == "LIVE" and codec == "json-chunks-v1" and created < cutoff and (
                location == f"native-files/{digest[:2]}/{digest}"
            ) and not db.execute(
                "SELECT 1 FROM artifact a WHERE id=? AND (" + protected + ")", (digest,)
            ).fetchone():
                aged.append((digest,))
                if len(aged) >= limit:
                    break
        if page and inspected == len(page) and len(page) < 256:
            next_cursor = ""
    if not page and not cursor and not aged:
        with closing(source.connection()) as db:
            if not db.execute("SELECT 1 FROM v2_native_exit WHERE not_before<=? LIMIT 1",(time.time(),)).fetchone():
                return report
    with closing(source.connection(write=True)) as db,db:
        db.execute('BEGIN IMMEDIATE');migrate(db)
        # A current-source snapshot can still read a formerly-current native
        # hash after its business owner changes. CDC pins protect the bridge,
        # not that older SQLite snapshot's files. Hold only this source's file
        # exit for the existing bounded snapshot lease; transport keeps moving.
        if db.execute("SELECT 1 FROM v2_source_consumers WHERE id LIKE 'snapshot:%' AND julianday(updated_at)>=julianday(?) LIMIT 1",((now-timedelta(hours=24)).isoformat(),)).fetchone():
            report['deferred']='current-source-snapshot'
            return report
        if not db.execute('SELECT 1 FROM v2_native_ref_scan LIMIT 1').fetchone() or db.execute('SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1').fetchone():return report
        early=db.execute("SELECT a.id FROM v2_native_exit e JOIN artifact a ON a.id=e.id WHERE e.not_before<0 "
                         "AND a.state='LIVE' ORDER BY e.not_before,e.id LIMIT ?",(limit,)).fetchall()
        candidates=[]
        for row in early:
            if db.execute('SELECT 1 FROM artifact a WHERE a.id=? AND ('+protected+')',row).fetchone():
                digest=row[0]
                if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('invalid native identity')
                p=root/'native-files'/digest[:2]/digest;q=quarantine/digest
                if p.is_symlink() or q.is_symlink():raise ValueError('linked native CAS')
                if not p.exists() and q.exists():p.parent.mkdir(exist_ok=True,parents=True);q.rename(p)
                elif q.exists():detach(q, discard)
                db.execute('DELETE FROM v2_native_exit WHERE id=?',row)
            else:candidates.append(row)
        if not early and (page or cursor):
            migrate_transport(db)
            current = db.execute(
                "SELECT cursor FROM v2_transport_gc_state WHERE kind='native-age-scan'"
            ).fetchone()
            if (current[0] if current else None) == (cursor_row[0] if cursor_row else None):
                candidates = [row for row in aged if db.execute(
                    "SELECT 1 FROM artifact a WHERE a.id=? AND state='LIVE' AND created_at<? "
                    "AND codec='json-chunks-v1' AND location='native-files/'||substr(id,1,2)||'/'||id "
                    "AND NOT (" + protected + ")", (*row, cutoff)
                ).fetchone()]
                db.execute(
                    "INSERT INTO v2_transport_gc_state(kind,cursor) VALUES('native-age-scan',?) "
                    "ON CONFLICT(kind) DO UPDATE SET cursor=excluded.cursor "
                    "WHERE cursor<>excluded.cursor", ("id:" + next_cursor,),
                )
        for digest, in candidates:
            if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('invalid native identity')
            p=root/'native-files'/digest[:2]/digest;q=quarantine/digest
            if p.is_symlink() or q.is_symlink():raise ValueError('linked native CAS')
            quarantine.mkdir(exist_ok=True)
            if p.exists():
                if q.exists():detach(q, discard)
                p.rename(q)
            if q.exists():
                db.execute("UPDATE artifact SET state='QUARANTINED' WHERE id=?",(digest,))
                db.execute('INSERT OR REPLACE INTO v2_native_exit VALUES(?,?)',(digest,time.time()+300))
                report['quarantined']+=1
            else:
                db.execute("UPDATE artifact SET state='DELETED' WHERE id=?",(digest,))
                db.execute('DELETE FROM v2_native_exit WHERE id=?',(digest,))
        for digest,not_before in db.execute('SELECT id,not_before FROM v2_native_exit WHERE not_before>=0 AND not_before<=? ORDER BY not_before LIMIT ?',(time.time(),limit)).fetchall():
            p=root/'native-files'/digest[:2]/digest;q=quarantine/digest
            if p.is_symlink() or q.is_symlink():raise ValueError('linked native quarantine')
            if db.execute('SELECT 1 FROM artifact a WHERE a.id=? AND ('+protected+')',(digest,)).fetchone():
                if not p.exists() and q.exists():p.parent.mkdir(parents=True,exist_ok=True);q.rename(p)
                elif q.exists():detach(q, discard)
                db.execute("UPDATE artifact SET state='LIVE' WHERE id=?",(digest,))
                db.execute('DELETE FROM v2_native_exit WHERE id=?',(digest,));continue
            if not_before<0 and db.execute("SELECT 1 FROM artifact WHERE id=? AND state='LIVE'",(digest,)).fetchone():
                continue  # Pending explicit exits have not entered quarantine yet.
            if not_before>time.time():continue
            # The existing reference and hold guards reject new protection in
            # DELETING. The canonical path is never removed here. A concurrent
            # publisher can prepare it and retry its reference after finalization.
            db.execute("UPDATE artifact SET state='DELETING' WHERE id=?", (digest,))
            claimed.append((digest, q))
            break
    for digest, q in claimed:
        allocated = 0
        try:
            for child in q.iterdir():
                if child.is_file():
                    info = child.stat()
                    allocated += getattr(info, 'st_blocks', (info.st_size + 511) // 512) * 512
            shutil.rmtree(q)
        except FileNotFoundError:
            if q.exists():
                raise
        with closing(source.connection(write=True)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("UPDATE artifact SET state='DELETED' WHERE id=? AND state='DELETING'", (digest,)).rowcount:
                db.execute('DELETE FROM v2_native_exit WHERE id=?', (digest,))
                report['deleted'] += 1
                report['allocated'] += allocated
    report['discarded'] += drain(discard)
    return report
