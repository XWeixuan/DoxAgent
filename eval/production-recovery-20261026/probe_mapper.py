"""Reproduce failed native projection without advancing any checkpoint."""
import json
import sqlite3
import traceback
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projectors import DomainProjectors

store=ReadStore('/data/read/active.json')
with store.connect() as db:
    events=[int(r[0]) for r in db.execute("SELECT event FROM gaps WHERE source='bus' ORDER BY CAST(event AS INTEGER) DESC LIMIT 4")]
source=SourceOutbox('/data/bus/bus.sqlite3','bus')
for seq in events:
    event=source.event(seq)
    if not event:
        print(seq,'unavailable');continue
    event['source']='bus'
    print('event',seq,event.get('table'),event.get('entity_id'))
    try:
        records,metrics=DomainProjectors(store)(event)
        print('mapped',len(records),len(metrics))
    except Exception:
        traceback.print_exc(limit=8)
