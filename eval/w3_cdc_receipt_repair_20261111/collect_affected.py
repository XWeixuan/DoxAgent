"""Bounded read-only comparison of failed source receipts and visible Case state."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import argparse
from datetime import UTC,datetime

os.nice(15);signal.alarm(30)
from doxagent.v2_read.outbox import SourceOutbox

root=Path('/data');runtime=root/'runtime/runtime.sqlite3'
alias=json.loads((root/'read/active.json').read_bytes());read=Path(alias['path'])
source=SourceOutbox(runtime,'runtime')
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path);args=p.parse_args()
result=dict(at=datetime.now(UTC).isoformat(),gaps=[],cases=[],unknown=[])
with sqlite3.connect(read.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1) as r:
    gaps=r.execute("SELECT event FROM gaps WHERE source='runtime' AND reason='KeyError' LIMIT 1001").fetchall()
    assert len(gaps)<=1000,'bounded audit exceeded'
    ids={c['case_id'] for c in json.loads(args.baseline.read_bytes())['cases']} if args.baseline else set()
    for (event,) in gaps:
        if not event.isdigit():continue
        packet=source.event(int(event),decode=False)
        if not packet:
            result['unknown'].append(dict(seq=int(event),reason='receipt_unavailable'));continue
        if packet['table_name']!='runtime_v2_cases':continue
        row=json.loads(packet['payload']);value=row.get('payload_json')
        if not isinstance(value,(dict,list)):continue
        ids.add(row['case_id'])
        result['gaps'].append(dict(seq=int(event),case_id=row['case_id'],at=packet['recorded_at'],payload_type=type(value).__name__))
    with sqlite3.connect(runtime.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1) as d:
        for cid in sorted(ids):
            row=d.execute("SELECT ticker,status,json_extract(payload_json,'$.w3_trade_decision.decision'),json_extract(payload_json,'$.w3_operations_status'),updated_at FROM runtime_v2_cases WHERE case_id=?",(cid,)).fetchone()
            if not row:
                result['unknown'].append(dict(case_id=cid,reason='source_case_unavailable'));continue
            current=r.execute("SELECT json_extract(payload,'$.status'),json_extract(payload,'$.w3_decision.value'),json_extract(payload,'$.w3_operations_status') FROM object_current WHERE kind='case' AND ticker=? AND id=?",(row[0],cid)).fetchone()
            result['cases'].append(dict(case_id=cid,ticker=row[0],source_status=row[1],source_decision=row[2],source_operations=row[3],source_updated_at=row[4],read_status=current[0] if current else None,read_decision=current[1] if current else None,read_operations=current[2] if current else None))
result['summary']=dict(affected_receipts=len(result['gaps']),affected_cases=len(result['cases']),unknown=len(result['unknown']),first=min((g['at'] for g in result['gaps']),default=None),last=max((g['at'] for g in result['gaps']),default=None),source_complete=sum(c['source_status']=='COMPLETED' and c['source_operations']=='COMMITTED' for c in result['cases']),read_stale=sum(c['source_status']!=c['read_status'] or c['source_decision']!=c['read_decision'] or c['source_operations']!=c['read_operations'] for c in result['cases']))
print(json.dumps(result,indent=2))
