"""Restore missing serving summaries from existing publications; never republish Bus news.

Run --apply with the normal projector stopped. Its existing writer lock fences
this operation. Original source checkpoints, failures and source data stay intact.
"""
import argparse
import json
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path

from doxagent.trade_execution.worker import WriterLock
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


def repair(read_db, bus_db, *, apply=False):
    store = ReadStore(read_db)
    report = dict(missing=0, restored=0, failures=[])
    guard = WriterLock(Path(str(store.path)+'.projector')) if apply else nullcontext()
    with guard:
        with store.connect() as db:
            known = {(r[0], r[1]) for r in db.execute(
                "SELECT ticker,id FROM object_current WHERE kind='message'")}
        with closing(sqlite3.connect(Path(bus_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            rows = db.execute(
                'SELECT s.ticker,m.stream_item_id,m.member_index,m.standard_message_id,m.data_json '
                'FROM stream_members m JOIN stream_items s ON s.stream_item_id=m.stream_item_id'
            ).fetchall()
        mapper = DomainProjectors(store)
        for ticker,stream,index,identity,payload in rows:
            if (ticker,identity) in known:
                continue
            report['missing'] += 1
            if not apply:
                continue
            try:
                event = dict(source='bus',table_name='stream_members',operation='INSERT',
                    seq=0,entity_id=json.dumps([stream,index]),row=dict(data_json=payload))
                records,metrics = mapper(event)
                store.ingest('bus-serving-recovery',stream+':'+str(index),records,
                             contributions=metrics)
                report['restored'] += 1
            except (ValueError, KeyError, OSError, sqlite3.OperationalError) as exc:
                report['failures'].append(dict(ticker=ticker,id=identity,error=str(exc)))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-db',required=True)
    parser.add_argument('--bus-db',required=True)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    print(json.dumps(repair(args.read_db,args.bus_db,apply=args.apply)))
