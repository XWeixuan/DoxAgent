"""Profile mapping with production reads; suppress content registration and all writes."""
import cProfile
import hashlib
import io
import json
import pstats
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.content_files import ContentFiles

def forbidden(*args,**kwargs):raise RuntimeError('DIAGNOSTIC_WRITE_FORBIDDEN')
ContentFiles.put=forbidden
class ReadOnlyStore(ReadStore):
    def __init__(self,path):
        super().__init__(path)
        self.content_calls=[]
    @contextmanager
    def connect(self,*,write=False,timeout=.2):
        if write:raise RuntimeError('DIAGNOSTIC_WRITE_FORBIDDEN')
        with super().connect(write=False,timeout=timeout) as db:
            db.execute('PRAGMA query_only=ON')
            deadline=time.monotonic()+2
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            yield db
    def put_content(self,ticker,body,kind='text/plain'):
        raw=body.encode(); sha=hashlib.sha256(raw).hexdigest()
        identity=hashlib.sha256(f'{ticker}:{kind}:{sha}'.encode()).hexdigest()
        self.content_calls.append({'bytes':len(raw),'sha256':sha})
        return {'content_id':identity,'content_type':kind,'size_bytes':len(raw),'sha256':sha}
    def search_content(self,ticker,reference,body=None):return '@body:'+reference['sha256']




from doxagent.v2_read.projector import native
from doxagent.v2_read.reference import project
import types,statistics
store=ReadOnlyStore('/data/read/serving-v4-20261018/v2.sqlite3');source=SourceOutbox('/data/runtime/runtime.sqlite3','runtime')
with source.connection() as db:
 coords=[r[0] for r in db.execute("SELECT seq FROM v2_source_outbox WHERE table_name='runtime_values' AND json_extract(entity_id,'$[0]')='reference_deliveries' ORDER BY seq DESC LIMIT 80")]
module=types.ModuleType("doxagent.v2_read.reference_baseline");module.__package__="doxagent.v2_read"
exec(compile('"""Index captured O3 input snapshots; never diff an old version against today\'s head."""\n\nfrom hashlib import sha256\n\nfrom doxagent.api_v2.dto import validate\n\nfrom .libraries import LibraryIndexer, materialization, present\nfrom .lifecycle import changed_paths\nfrom .repository import encode\n\n\ndef project(store, value):\n    ticker, identity, day = value["ticker"], value["delta_id"], value["semantic_day"]\n    evidence = {"kind": "reference_input", "ticker": ticker, "id": identity, "data": value}\n    prior = store.get("reference_day_chain", ticker, day)\n    if prior and prior["prepared_at"] > value["prepared_at"]:\n        return [evidence]\n    if prior:\n        value = {\n            **value,\n            "before": prior["before"],\n            "before_root": prior.get("before_root", prior["root"]),\n        }\n    records, refs, snapshot_ids, events = [evidence], {}, {}, {}\n    records.append({"kind": "reference_day_chain", "ticker": ticker, "id": day, "data": value})\n    for side in ("before", "after"):\n        captured = value[side]\n        snapshot_ids[side] = sha256(encode([ticker, captured]).encode()).hexdigest()\n        ref,indexed,by_id,facts = LibraryIndexer(value.get(side + \'_root\',value[\'root\'])).reference(store,ticker,captured)\n        refs[side] = ref\n        records.extend(indexed)\n        proof={\'root\':str(LibraryIndexer(value.get(side + \'_root\',value[\'root\'])).root), \'snapshot\':snapshot_ids[side], \'library\':ref, \'contract\':2}\n        marker=materialization(ticker,\'reference:\'+snapshot_ids[side],proof)\n        ready=store.get(\'projection_materialization\',ticker,marker[\'id\'])==proof\n        if not ready:\n            expected=[]\n            for ev in captured[\'events\']:\n                parent=snapshot_ids[side]+\':\'+ev[\'event_id\']\n                expected.append((\'reference_event\',ticker,parent))\n                expected.extend((\'fact\',ticker,parent+\':\'+facts[f[\'fact_id\']]) for f in ev[\'facts\'])\n            ready=present(store,expected)\n            if ready and captured[\'events\']:\n                # Captured hashes omit root; a branch may reuse an E-number\n                # with another birth identity, including events with no facts.\n                expected_refs=[(snapshot_ids[side]+\':\'+ev[\'event_id\'],by_id[ev[\'event_id\']][\'event_key\'],ref[\'library_snapshot_id\']) for ev in captured[\'events\']]\n                with store.connect() as db:\n                    matches=db.execute("SELECT count(*) FROM json_each(?) q CROSS JOIN object_current c INDEXED BY current_identity ON c.kind=\'reference_event\' AND c.ticker=? AND c.id=json_extract(q.value,\'$[0]\') WHERE json_extract(c.payload,\'$.event_key\')=json_extract(q.value,\'$[1]\') AND json_extract(c.payload,\'$.library.library_snapshot_id\')=json_extract(q.value,\'$[2]\')",(encode(expected_refs),ticker)).fetchone()[0]\n                ready=matches==len(expected_refs)\n        records.append(marker)\n        events[side] = {}\n        for event in captured["events"]:\n            key = by_id[event["event_id"]]["event_key"]\n            revision = sha256(encode([key, event]).encode()).hexdigest()\n            events[side][key] = {"event": event, "revision": revision}\n            parent = snapshot_ids[side] + ":" + event["event_id"]\n            detail = {\n                "event_key": key,\n                "library": ref,\n                "reference_snapshot_id": snapshot_ids[side],\n                "event_revision_id": revision,\n                "event": {k: v for k, v in event.items() if k != "facts"},\n            }\n            if not ready: records.append(\n                {"kind": "reference_event", "ticker": ticker, "id": parent, "data": detail}\n            )\n            for ordinal, fact in enumerate(event["facts"] if not ready else []):\n                fact_key = facts[fact["fact_id"]]\n                records.append(\n                    {\n                        "kind": "fact",\n                        "ticker": ticker,\n                        "id": parent + ":" + fact_key,\n                        "parent": parent,\n                        "sort": f"{999999999 - ordinal:09d}",\n                        "data": validate(\n                            "FactRow",\n                            {\n                                "fact_key": fact_key,\n                                "fact": fact,\n                                "ordinal": ordinal,\n                                "fact_revision_id": sha256(\n                                    encode([fact_key, fact]).encode()\n                                ).hexdigest(),\n                                "lifecycle": "ACTIVE",\n                                "member_of_event": True,\n                            },\n                        ),\n                    }\n                )\n    changes = []\n    for key in sorted(set(events["before"]) | set(events["after"])):\n        before, after = events["before"].get(key), events["after"].get(key)\n        event_id = (after or before)["event"]["event_id"]\n        if (\n            before\n            and after\n            and value["before"]["rendered"][event_id] == value["after"]["rendered"][event_id]\n        ):\n            continue\n        selected = "after" if after else "before"\n        chosen = after or before\n        change_id = sha256(encode([identity, key]).encode()).hexdigest()\n        change = validate(\n            "ReferenceDeltaItem",\n            {\n                "change_id": change_id,\n                "type": "add" if not before else "remove" if not after else "modify",\n                "event_key": key,\n                "event_id": event_id,\n                "title": chosen["event"]["title"],\n                "before_revision_id": before["revision"] if before else None,\n                "after_revision_id": after["revision"] if after else None,\n                "detail_revision_id": chosen["revision"],\n                "detail_library_snapshot_id": refs[selected]["library_snapshot_id"],\n                "detail_library_version": refs[selected]["library_version"],\n                "changed_paths": changed_paths(\n                    before["event"] if before else None, after["event"] if after else None\n                ),\n                "summary": "Reference View 输入变化（非 Policy 内容变更）",\n            },\n        )\n        changes.append(change)\n        records.append(\n            {\n                "kind": "reference_change",\n                "ticker": ticker,\n                "id": identity + ":" + change_id,\n                "parent": identity,\n                "data": change,\n            }\n        )\n        records.append(\n            {\n                "kind": "reference_change_sides",\n                "ticker": ticker,\n                "id": identity + ":" + change_id,\n                "data": {\n                    side: snapshot_ids[side] + ":" + event_id if events[side].get(key) else None\n                    for side in ("before", "after")\n                },\n            }\n        )\n    data = {\n        "delta_id": identity,\n        "semantic_day": day,\n        "from_library_version": value["before"]["version"],\n        "to_library_version": value["after"]["version"],\n        "from_library_snapshot_id": refs["before"]["library_snapshot_id"]\n        if refs["before"]\n        else None,\n        "to_library_snapshot_id": refs["after"]["library_snapshot_id"],\n        "before_reference_snapshot_id": snapshot_ids["before"],\n        "after_reference_snapshot_id": snapshot_ids["after"],\n    }\n    records.append({"kind": "reference_delta", "ticker": ticker, "id": identity, "data": data})\n    state = (\n        "PENDING"\n        if value["status"] == "PREPARED"\n        else "FAILED"\n        if value["status"] == "FAILED"\n        else ("AVAILABLE" if changes else "NO_CHANGE")\n    )\n    day_data = {\n        k: data[k]\n        for k in (\n            "delta_id",\n            "semantic_day",\n            "from_library_version",\n            "to_library_version",\n            "from_library_snapshot_id",\n            "to_library_snapshot_id",\n        )\n    }\n    day_data.update(state=state, reason="SOURCE_GAP" if state == "FAILED" else None)\n    records.append(\n        {\n            "kind": "delta_day",\n            "ticker": ticker,\n            "id": day,\n            "sort": day,\n            "day": day,\n            "data": validate("DeltaDay", day_data),\n        }\n    )\n    return records\n',"reference_baseline","exec"),module.__dict__)

out=[];tickers=set()
with store.projection_session():
 for seq in coords:
  e=source.event(seq,serving=True,projection=True);v=native(e)
  if v['ticker'] in tickers:continue
  prior=store.get('reference_day_chain',v['ticker'],v['semantic_day'])
  if not prior or prior['delta_id']!=v['delta_id'] or not store.get('projection_materialization',v['ticker'],'reference-delta:'+v['delta_id']):continue
  v={**v,'status':'FAILED' if v['status']!='FAILED' else 'SUCCEEDED'}
  before=[];after=[]
  for _ in range(3):
   t=time.perf_counter();full=module.project(store,v);before.append(time.perf_counter()-t)
   t=time.perf_counter();fast=project(store,v);after.append(time.perf_counter()-t)
  indexed={(r['kind'],r['ticker'],r['id']):r for r in fast};diff=0
  for r in full:
   key=r['kind'],r['ticker'],r['id']
   if key in indexed:diff+=indexed[key]!=r
   else:diff+=store.get(*key)!=r['data']
  out.append(dict(seq=seq,full_seconds=before,fast_seconds=after,full_records=len(full),fast_records=len(fast),payload_differences=diff))
  tickers.add(v['ticker'])
  if len(out)==2:break
print(json.dumps(out))
