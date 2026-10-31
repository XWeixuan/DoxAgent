"""Explicit small dashboard upgrade; stop Collector/Bus for this release step.

No article/message history backfill, no new index and no ordinary-startup work.
"""
import json
import os
import sqlite3
from pathlib import Path

from .live_repository import LiveRepository
from .collection_plane.store import path_for
from doxagent.v2_read.outbox import SourceOutbox


def migrate(path):
    with sqlite3.connect(path,timeout=2) as db:
        db.execute('BEGIN IMMEDIATE')
        LiveRepository.migrate(db)
    # Regenerate receipt triggers so the two new scalars enter the normal CDC.
    SourceOutbox(path,'bus').migrate()
    updated=0
    acquisition=path_for(path)
    if Path(acquisition).is_file():
        with sqlite3.connect(acquisition,timeout=2) as db:
            updated=db.execute("UPDATE units SET head_json=json_set(head_json,'$.disabled_at',"
                "(SELECT dispatch_at FROM cycles INDEXED BY cycles_schedule WHERE status='SCHEDULE' "
                "AND binding_id=units.binding_id AND unit=units.unit ORDER BY dispatch_at DESC LIMIT 1)) "
                "WHERE required=-1 AND json_extract(head_json,'$.disabled_at') IS NULL").rowcount
    return {'delivery_anchors':'installed','retired_heads_initialized':updated,'history_backfill':False}


if __name__=='__main__':
    print(json.dumps(migrate(os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH'])))
