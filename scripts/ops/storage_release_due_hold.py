"""One-off release window: defer only unstarted Maintenance, never Case/W3.

Existing model jobs continue. The hold expires by wall clock even if the release
shell dies; restore keeps each Task's real due time, inputs, identity and lease.
This is an operator cutover aid, not a production scheduling strategy.
"""
import argparse
from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
from pathlib import Path
import sqlite3

TABLE='storage_release_due_hold_20261124'
TRIGGERS=[TABLE+'_insert',TABLE+'_pending']


def connect(path):return sqlite3.connect(path,timeout=.01)


def hold(path,*,now=None,minutes=10):
    now=now or datetime.now(UTC);until=now+timedelta(minutes=minutes)
    stamp=until.isoformat();expires=int(until.timestamp())
    with closing(connect(path)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone():raise ValueError('restore the existing release hold first')
        db.execute('CREATE TABLE '+TABLE+'(task_id TEXT PRIMARY KEY,original_due TEXT NOT NULL,held_due TEXT NOT NULL)')
        body=(" WHEN NEW.kind='MAINTENANCE' AND NEW.status='PENDING' AND NEW.due_at<'"+stamp+"' AND unixepoch('now')<"+str(expires)+
            ' BEGIN INSERT INTO '+TABLE+" VALUES(NEW.id,NEW.due_at,'"+stamp+"') ON CONFLICT(task_id) DO UPDATE SET original_due=excluded.original_due,held_due=excluded.held_due; "
            "UPDATE runtime_tasks SET due_at='"+stamp+"' WHERE id=NEW.id; END")
        db.execute('CREATE TRIGGER '+TRIGGERS[0]+' AFTER INSERT ON runtime_tasks'+body)
        db.execute('CREATE TRIGGER '+TRIGGERS[1]+' AFTER UPDATE OF status,due_at ON runtime_tasks'+body)
        identities=[r[0] for r in db.execute("SELECT id FROM runtime_tasks WHERE kind='MAINTENANCE' AND status='PENDING' AND due_at<?",(stamp,))]
    for offset in range(0,len(identities),16):
        with closing(connect(path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            for identity in identities[offset:offset+16]:
                # Assign the unchanged real due first; the exact trigger records
                # it and substitutes only this finite release-window deadline.
                db.execute("UPDATE runtime_tasks SET due_at=due_at WHERE id=? AND kind='MAINTENANCE' AND status='PENDING' AND due_at<?",(identity,stamp))
    return {'maintenance_tasks_seen':len(identities),'expires_at':stamp,'case_w3_admission_unchanged':True}


def restore(path):
    with closing(connect(path)) as db,db:
        db.execute('BEGIN IMMEDIATE')
        for trigger in TRIGGERS:db.execute('DROP TRIGGER IF EXISTS '+trigger)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone():return {'restored':0}
        rows=db.execute('SELECT task_id,original_due,held_due FROM '+TABLE).fetchall()
    count=0
    for offset in range(0,len(rows),16):
        with closing(connect(path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            for identity,original,held in rows[offset:offset+16]:
                count+=db.execute("UPDATE runtime_tasks SET due_at=? WHERE id=? AND status='PENDING' AND due_at=?",(original,identity,held)).rowcount
    with closing(connect(path)) as db,db:db.execute('DROP TABLE '+TABLE)
    return {'restored':count,'observed':len(rows)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['hold','restore']);parser.add_argument('--runtime',type=Path,required=True)
    args=parser.parse_args();path=args.runtime.resolve(strict=True)
    print(json.dumps(hold(path) if args.action=='hold' else restore(path)))
