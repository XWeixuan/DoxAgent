"""Read-only backlog receipt; run again after a meaningful observation interval."""
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
with closing(sqlite3.connect((root/'read/v2.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
    positions=dict(db.execute('SELECT source,position FROM checkpoints'))
report={'at':datetime.now(UTC).isoformat(),'sources':{}}
for source in ['bus','runtime']:
    with closing(sqlite3.connect((root/source/(source+'.sqlite3')).as_uri()+'?mode=ro',uri=True)) as db:
        head=db.execute('SELECT head FROM v2_source_state WHERE source=?',(source,)).fetchone()[0]
    report['sources'][source]=dict(head=head,checkpoint=positions[source],backlog=head-positions[source])
with (Path('/home/ubuntu/disk-repair-20261013')/'projection-progress.jsonl').open('a') as file:
    file.write(json.dumps(report)+'\n')
print(json.dumps(report),flush=True)
