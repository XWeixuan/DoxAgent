"""Diagnose the six recorded historical projection gaps without production writes."""
import json
import traceback
from pathlib import Path
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.outbox import SourceOutbox

class ReadOnlyStore(ReadStore):
    def connect(self, *, write=False, **kwargs):
        if write:
            raise RuntimeError('read-only replay attempted a write')
        return super().connect(write=False, **kwargs)
    def put_content(self,*args,**kwargs):
        raise RuntimeError('read-only replay attempted content creation')

store=ReadOnlyStore('/data/read/v2.sqlite3')
source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
mapper=DomainProjectors(store)
results=[]
for seq in (1240145,1240148,1240191,1240193,1257748,1257924):
    event={**source.event(seq),'source':'runtime'}
    try:
        records,metrics=mapper(event)
        results.append(dict(seq=seq,passed=True,records=len(records),metrics=len(metrics)))
    except Exception as exc:
        results.append(dict(seq=seq,passed=False,error=str(exc),traceback=traceback.format_exc()))
print(json.dumps(results,indent=2))
