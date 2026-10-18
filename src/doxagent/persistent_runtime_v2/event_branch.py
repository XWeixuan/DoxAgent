"""Isolated selected-version Event branches; no inherited CDC or process history."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from doxagent.event_library.reference_review import classify_review,event_review_anchor
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.trade_execution.worker import WriterLock
from doxagent.v2_read.maintenance import atomic_json


def branch_library(source: Path,target: Path,ticker: str,base: int) -> EventLibraryRepository:
    with WriterLock(target.with_suffix('.branch')):
        if target.exists():return EventLibraryRepository(target)
        target.parent.mkdir(parents=True,exist_ok=True)
        staging=target.with_suffix('.preparing')
        # Only an interrupted staging owned by this exact, OS-fenced branch.
        for suffix in ('','-wal','-shm'):
            Path(str(staging)+suffix).unlink(missing_ok=True)
        helper=EventLibraryRepository(staging)
        with closing(sqlite3.connect(staging.resolve().as_uri(),uri=True)) as db:
            db.row_factory=sqlite3.Row
            db.execute('ATTACH DATABASE ? AS origin',(source.resolve().as_uri()+'?mode=ro',))
            db.execute('BEGIN IMMEDIATE')
            version=db.execute('SELECT * FROM origin.library_versions WHERE ticker=? AND version=?',(ticker,base)).fetchone()
            if version is None:raise ValueError('branch base publication unavailable')
            head=db.execute('SELECT published_version FROM origin.library_heads WHERE ticker=?',(ticker,)).fetchone()[0]
            def copy(table,where,args=()):
                columns=','.join(r[1] for r in db.execute('PRAGMA main.table_info('+table+')'))
                db.execute('INSERT INTO main.'+table+'('+columns+') SELECT '+columns+' FROM origin.'+table+' WHERE '+where,args)
            copy('library_versions','ticker=? AND version=?',(ticker,base))
            copy('library_heads','ticker=?',(ticker,))
            db.execute('UPDATE library_heads SET published_version=? WHERE ticker=?',(base,ticker))
            copy('id_counters','ticker=?',(ticker,))
            for entity,column in (('events','event_no'),('facts','fact_no')):
                copy('canonical_'+entity,'ticker=? AND created_version<=?',(ticker,base))
                for suffix in ('revisions','states'):
                    table='canonical_'+entity[:-1]+'_'+suffix
                    copy(table,'ticker=? AND library_version=(SELECT max(p.library_version) FROM origin.'+table+' p WHERE p.ticker=origin.'+table+'.ticker AND p.'+column+'=origin.'+table+'.'+column+' AND p.library_version<=?)',(ticker,base))
            for table in ('event_fact_memberships','event_relations'):
                copy(table,'ticker=? AND valid_from_version<=? AND (valid_to_version IS NULL OR valid_to_version>=?)',(ticker,base,base))
                db.execute('UPDATE '+table+' SET valid_to_version=NULL')
            # A current pending batch remains resumable; old published payloads
            # leave the branch. Small last-application mappings retain dedup.
            copy('delta_batches','ticker=? AND published_version IS NULL AND base_version<=?',(ticker,base if head==base else base-1))
            copy('delta_items','batch_id IN (SELECT batch_id FROM main.delta_batches)')
            from .retained_events import atomic_at
            db.executemany('INSERT INTO runtime_atomic_mappings VALUES(?,?,?,?,?,?,?,?,?)',
                           [tuple(row) for row in atomic_at(db,ticker,base)])
            # Replace generated target birth IDs with their exact source IDs.
            db.execute("UPDATE v2_native_identities SET provenance='PROVENANCE_UNVERIFIED'")
            db.execute("INSERT OR REPLACE INTO v2_native_identities SELECT s.* FROM origin.v2_native_identities s JOIN main.v2_native_identities t ON t.collection=s.collection AND t.native_key=s.native_key")
            if head==base:
                copy('reference_review_schedule','ticker=?',(ticker,))
            else:
                at=datetime.fromisoformat(version['published_at'])
                for row in db.execute('SELECT event_no FROM canonical_events WHERE ticker=?',(ticker,)).fetchall():
                    event=helper._event_from_connection(db,ticker,row[0],base)
                    anchor=event_review_anchor(event)
                    mode,reason,next_at=classify_review(anchor=anchor,as_of=at,include_in_reference_view=event.include_in_reference_view)
                    helper._upsert_review_schedule(db,ticker=ticker,event_no=row[0],anchor=anchor,reviewed_at=at,next_at=next_at,mode=mode,reason=reason)
            # Compare the complete selected native event representation, including
            # fact membership/state. Birth/provenance rows were copied above.
            original=EventLibraryRepository(source,read_only=True)
            expected=[e.model_dump(mode='json') for e in original.published_events(ticker,base)]
            actual=[helper._event_from_connection(db,ticker,r[0],base).model_dump(mode='json') for r in db.execute("SELECT e.event_no FROM canonical_events e JOIN canonical_event_states s USING(ticker,event_no) WHERE e.ticker=? AND s.status='ACTIVE' ORDER BY e.event_no",(ticker,)).fetchall()]
            if expected!=actual:raise ValueError('selected Event snapshot differs')
            db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        os.replace(staging,target)
        atomic_json(str(target)+'.branch.json',{'source':str(source.resolve()),'base_version':base,'ticker':ticker,'selected_event_count':len(actual),'format':'doxagent.minimal-event-branch.1'})
        return EventLibraryRepository(target)
