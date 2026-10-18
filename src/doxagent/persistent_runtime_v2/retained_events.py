"""Native snapshots for historical branch references, without inherited CDC/Delta payloads."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.v2_read.maintenance import atomic_json


MAP_COLUMNS=('runtime_scope','runtime_atomic_id','runtime_atomic_version','runtime_signature',
             'disposition','canonical_event_no','canonical_fact_no','last_delta_batch','updated_at')


def atomic_at(db, ticker, version):
    head=db.execute('SELECT published_version FROM origin.library_heads WHERE ticker=?',(ticker,)).fetchone()[0]
    if head==version:
        return list(db.execute('SELECT '+','.join(MAP_COLUMNS)+' FROM origin.runtime_atomic_mappings'))
    if db.execute("SELECT 1 FROM origin.sqlite_master WHERE name='retained_runtime_mappings'").fetchone():
        return list(db.execute('SELECT '+','.join(MAP_COLUMNS)+' FROM origin.retained_runtime_mappings WHERE version=?',(version,)))
    return list(db.execute("SELECT i.runtime_scope,i.runtime_atomic_id,i.runtime_atomic_version,i.runtime_signature,i.resolution,i.target_event_no,i.target_fact_no,i.batch_id,b.updated_at FROM origin.delta_items i JOIN origin.delta_batches b ON b.batch_id=i.batch_id WHERE b.ticker=? AND b.published_version<=? AND NOT EXISTS(SELECT 1 FROM origin.delta_items n JOIN origin.delta_batches nb ON nb.batch_id=n.batch_id WHERE n.runtime_scope=i.runtime_scope AND n.runtime_atomic_id=i.runtime_atomic_id AND nb.published_version<=? AND (nb.published_version>b.published_version OR nb.published_version=b.published_version AND n.rowid>i.rowid))",(ticker,version,version)))


def snapshot_digest(repository,ticker,version):
    """Include inactive native Events, fact status/redirect and exact birth IDs."""
    digest=hashlib.sha256()
    def add(value):digest.update(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode());digest.update(b'\n')
    with repository._read() as db:
        events=[r[0] for r in db.execute('SELECT event_no FROM canonical_events WHERE ticker=? AND created_version<=? ORDER BY event_no',(ticker,version))]
        facts=[r[0] for r in db.execute('SELECT fact_no FROM canonical_facts WHERE ticker=? AND created_version<=? ORDER BY fact_no',(ticker,version))]
        for number in events:
            add(repository._event_from_connection(db,ticker,number,version).model_dump(mode='json'))
        for number in facts:
            row=db.execute('SELECT status,redirect_to_fact_no FROM canonical_fact_states WHERE ticker=? AND fact_no=? AND library_version<=? ORDER BY library_version DESC LIMIT 1',(ticker,number,version)).fetchone()
            add(['fact_status',number,tuple(row) if row else None])
        for collection,numbers in (('canonical_events',events),('canonical_facts',facts),('library_versions',[version])):
            for number in numbers:
                row=db.execute('SELECT identity,provenance FROM v2_native_identities WHERE collection=? AND native_key=json_array(?,?)',(collection,ticker,number)).fetchone()
                add([collection,number,tuple(row) if row else None])
    compiler=EventLibraryViewCompiler(repository)
    add(compiler.known_event_index(ticker,version));add(compiler.reference_view(ticker,version))
    selected,at=repository.published_metadata(ticker,version);add([selected,at.isoformat() if at else None])
    return digest.hexdigest()


def build(source,target,ticker,versions):
    source=Path(source).resolve(strict=True);target=Path(target).resolve()
    if target.exists() or source==target:raise ValueError('retained native target must be new')
    original=EventLibraryRepository(source,read_only=True)
    head=original.published_version(ticker);versions=sorted(v for v in set(map(int,versions))|{head} if v!=0)
    if not versions or min(versions)<1:raise ValueError('retained branch needs published versions')
    target.parent.mkdir(parents=True,exist_ok=True);EventLibraryRepository(target)
    with closing(sqlite3.connect(target.as_uri(),uri=True)) as db,db:
        db.row_factory=sqlite3.Row;db.execute('ATTACH DATABASE ? AS origin',(source.as_uri()+'?mode=ro',));db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT count(*) FROM origin.library_heads').fetchone()[0]!=1:raise ValueError('historical branch must have one ticker')
        db.execute('CREATE TEMP TABLE selected_versions(version INTEGER PRIMARY KEY)')
        db.executemany('INSERT INTO selected_versions VALUES(?)',[(v,) for v in versions])
        def copy(table,predicate='1',args=()):
            columns=','.join(r[1] for r in db.execute('PRAGMA main.table_info('+table+')'))
            db.execute('INSERT INTO main.'+table+'('+columns+') SELECT '+columns+' FROM origin.'+table+' WHERE '+predicate,args)
        copy('library_versions','ticker=? AND version IN (SELECT version FROM selected_versions)',(ticker,))
        if db.execute('SELECT count(*) FROM library_versions').fetchone()[0]!=len(versions):raise ValueError('requested historical version not retained')
        copy('library_heads','ticker=?',(ticker,));copy('id_counters','ticker=?',(ticker,))
        for entity,column in (('events','event_no'),('facts','fact_no')):
            copy('canonical_'+entity,'ticker=?',(ticker,))
            for suffix in ('revisions','states'):
                table='canonical_'+entity[:-1]+'_'+suffix
                copy(table,'ticker=? AND EXISTS(SELECT 1 FROM selected_versions v WHERE origin.'+table+'.library_version=(SELECT max(p.library_version) FROM origin.'+table+' p WHERE p.ticker=origin.'+table+'.ticker AND p.'+column+'=origin.'+table+'.'+column+' AND p.library_version<=v.version))',(ticker,))
        for table in ('event_fact_memberships','event_relations'):
            copy(table,'ticker=? AND EXISTS(SELECT 1 FROM selected_versions v WHERE valid_from_version<=v.version AND (valid_to_version IS NULL OR valid_to_version>=v.version))',(ticker,))
        copy('reference_review_schedule','ticker=?',(ticker,))
        copy('reference_review_history','ticker=? AND reviewed_at=(SELECT max(h.reviewed_at) FROM origin.reference_review_history h WHERE h.ticker=origin.reference_review_history.ticker AND h.event_no=origin.reference_review_history.event_no)',(ticker,))
        # Keep only real requested before/after pairs, not every copied process
        # delta. Runtime reference deliveries retain their actual frozen inputs.
        copy('reference_view_deltas','ticker=? AND from_library_version IN (SELECT version FROM selected_versions) AND to_library_version IN (SELECT version FROM selected_versions)',(ticker,))
        db.execute('CREATE TABLE retained_runtime_mappings(version INTEGER,'+','.join(c+' '+('INTEGER' if c in {'runtime_atomic_version','canonical_event_no','canonical_fact_no'} else 'TEXT') for c in MAP_COLUMNS)+',PRIMARY KEY(version,runtime_scope,runtime_atomic_id))')
        for version in versions:
            rows=atomic_at(db,ticker,version)
            db.executemany('INSERT INTO retained_runtime_mappings VALUES('+','.join('?' for _ in range(10))+')',[(version,*tuple(row)) for row in rows])
            if version==head:db.executemany('INSERT INTO runtime_atomic_mappings VALUES('+','.join('?' for _ in range(9))+')',[tuple(row) for row in rows])
        db.execute('CREATE TABLE retired_delta_receipts(batch_id TEXT PRIMARY KEY,ticker TEXT,source_snapshot_id TEXT,base_version INTEGER,published_version INTEGER,bundle_hash TEXT,source_bundle_hash TEXT,publication_json TEXT,updated_at TEXT)')
        if db.execute("SELECT 1 FROM origin.sqlite_master WHERE name='retired_delta_receipts'").fetchone():copy('retired_delta_receipts')
        db.execute('INSERT OR IGNORE INTO retired_delta_receipts SELECT batch_id,ticker,source_snapshot_id,base_version,published_version,bundle_hash,source_bundle_hash,publication_json,updated_at FROM origin.delta_batches WHERE ticker=? AND published_version IS NOT NULL',(ticker,))
        db.execute("UPDATE v2_native_identities SET provenance='PROVENANCE_UNVERIFIED'")
        db.execute('INSERT OR REPLACE INTO v2_native_identities SELECT s.* FROM origin.v2_native_identities s JOIN main.v2_native_identities t ON t.collection=s.collection AND t.native_key=s.native_key')
        db.commit();db.execute('DETACH DATABASE origin')
        db.execute('PRAGMA main.wal_checkpoint(TRUNCATE)').fetchall();db.execute('PRAGMA main.journal_mode=DELETE').fetchall()
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('native compact integrity failed')
    compact=EventLibraryRepository(target,read_only=True);proof={}
    for version in versions:
        before=snapshot_digest(original,ticker,version);after=snapshot_digest(compact,ticker,version)
        if before!=after:raise ValueError('retained native snapshot/birth differs')
        proof[str(version)]=before
    report=dict(format='doxagent.retained-native-branch.1',ticker=ticker,versions=versions,
                source=str(source),target=str(target),snapshot_sha256=proof,source_bytes=source.stat().st_size,target_bytes=target.stat().st_size,verified=True)
    atomic_json(str(target)+'.native-retained.json',report)
    return report
