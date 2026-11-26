"""Temporary, exact O3 admission fence while an old Scheduler drains O2.

Remove after the new Scheduler owns maintenance_control. Does not cancel O2,
W3, initialization, existing receipts or any running model turn.
"""
import argparse
import json
import sqlite3

NAME = 'storage_o3_cutover_20261123'


def apply(path, *, remove=False):
    with sqlite3.connect(path, timeout=.1) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DROP TRIGGER IF EXISTS '+NAME)
        if remove:
            pass
        else:
            db.execute("CREATE TRIGGER IF NOT EXISTS "+NAME+" BEFORE INSERT ON jobs "
                "WHEN CASE WHEN NEW.priority<>0 OR NEW.request IS NULL THEN 0 ELSE "
                "json_extract(NEW.request,'$.run_id') GLOB 'runtime-maintain-*-o3*' "
                "AND NOT EXISTS(SELECT 1 FROM jobs WHERE id=NEW.id) END "
                "BEGIN SELECT RAISE(ABORT,'O3_PAUSED_FOR_STORAGE_CUTOVER'); END")
        return {'fence_present': bool(db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (NAME,)).fetchone())}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('path');p.add_argument('--remove', action='store_true')
    args=p.parse_args()
    print(json.dumps(apply(args.path, remove=args.remove)))
