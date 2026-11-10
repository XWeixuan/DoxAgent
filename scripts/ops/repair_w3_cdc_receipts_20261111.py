"""Finite explicit-gap replay through the existing mapper and Read watermarks.

No source/Runtime/Worker writes, model calls or checkpoint reset. The caller
owns the same OS projector lock as the normal ProjectionWorker.
"""
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import signal
import time


def repair(store,source,coordinates,*,budget=60):
    from doxagent.v2_read.formal import FormalProjectors
    assert source.source=='runtime'
    coordinates=sorted({int(n) for n in coordinates},reverse=True)
    if not 1<=len(coordinates)<=1000:raise ValueError('explicit list must contain 1 to 1000 receipts')
    result=dict(at=datetime.now(UTC).isoformat(),requested=len(coordinates),repaired=[],skipped=[],failures=[],deferred=[])
    mapper=FormalProjectors(store);deadline=time.monotonic()+budget
    with store.projection_session():
        for index,seq in enumerate(coordinates):
            if time.monotonic()>=deadline:
                result['deferred']=coordinates[index:];break
            with store.connect() as db:
                gap=db.execute("SELECT reason FROM gaps WHERE source='runtime' AND event=?",(str(seq),)).fetchone()
            if not gap or gap[0]!='KeyError':
                result['skipped'].append(seq);continue
            try:
                event=source.event(seq,serving=True)
                if not event or event['table_name']!='runtime_v2_cases':
                    raise ValueError('selected receipt is not an available Runtime Case')
                event['source']='runtime'
                records,counts=mapper(event)
                store.ingest('runtime',str(seq)+':repair:1',records,contributions=counts,
                             position=seq,at=event['recorded_at'],resolved_gap=str(seq))
                with store.connect() as db:
                    remaining=db.execute("SELECT 1 FROM gaps WHERE source='runtime' AND event=?",(str(seq),)).fetchone()
                if remaining:raise ValueError('repair did not resolve selected gap')
                result['repaired'].append(seq)
            except (ValueError,KeyError,TypeError,OSError) as exc:
                result['failures'].append(dict(seq=seq,error=type(exc).__name__,reason=str(exc)[:200]))
    return result


def main():
    import argparse
    from doxagent.trade_execution.worker import WriterLock
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.repository import ReadStore
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--coordinates',type=Path,required=True)
    p.add_argument('--budget',type=float,default=60);args=p.parse_args()
    os.nice(15);signal.alarm(int(args.budget)+30)
    alias=json.loads(Path('/data/read/active.json').read_bytes());store=ReadStore(alias['path'])
    source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
    with WriterLock(Path(str(store.path)+'.projector')):
        print(json.dumps(repair(store,source,json.loads(args.coordinates.read_bytes()),budget=args.budget)),flush=True)


if __name__=='__main__':main()
