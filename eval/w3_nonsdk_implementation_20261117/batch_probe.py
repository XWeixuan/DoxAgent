"""Read-only real versions; compare original object reader to batch hydration."""
import __future__
import json
import time
from pathlib import Path
import sqlite3
from doxagent.event_library import repository as module
from doxagent.v2_read.native_content import NativeContent
from doxagent.persistent_runtime_v2.daily_index import event_provider,fact_candidate,fact_content

scope=dict(vars(module))
exec(compile(Path('/probe/legacy_published_events.py').read_text(),'<legacy>','exec',flags=__future__.annotations.compiler_flag),scope)
legacy=scope['published_events'];fast=module.EventLibraryRepository.published_events
path=Path('/data/runtime/runtime.sqlite3');out=[]
for cid in ('case_fbef85346c3e4a869bcf876fe7e00333','case_ea871530ed5b46f5a6f1eab6c4849a4a'):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.1) as db:
        value=json.loads(db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(cid,)).fetchone()[0])
    frozen=NativeContent(path).decode(value['frozen_inputs']);pin=frozen['w1_daily_index']
    provider=event_provider(None,value['version_pin']['event_library_root']);repo=provider.reader._repository('INTC');version=value['version_pin']['event_library_version']
    seen={fact_content(fact_candidate(e,f)) for e,f in event_provider(None,pin['event_library_root']).published_facts('INTC',pin['event_library_version'])}
    from doxagent.persistent_runtime_v2.schema import RuntimeFactCandidate
    seen|={fact_content(RuntimeFactCandidate.model_validate(a['candidate'])) for a in frozen['provisional']}
    samples=[]
    for name,method in [('legacy',legacy),('batch',fast)]:
        module.EventLibraryRepository.published_events=method
        at,cpu=time.perf_counter(),time.process_time()
        events=repo.published_events('INTC',version)
        read=dict(wall_ms=(time.perf_counter()-at)*1000,cpu_ms=(time.process_time()-cpu)*1000)
        at,cpu=time.perf_counter(),time.process_time()
        bridge=provider.new_library_facts('INTC',pin['event_library_version'],version,excluded_contents=seen)
        samples.append(dict(name=name,read=read,bridge=dict(wall_ms=(time.perf_counter()-at)*1000,cpu_ms=(time.process_time()-cpu)*1000),
                            objects=[e.model_dump(mode='json') for e in events],admissions=[(v,e.event_id,f.fact_id,fact_content(fact_candidate(e,f))) for v,e,f in bridge]))
    out.append(dict(case_id=cid,version=version,events=len(samples[0]['objects']),eligible=len(samples[0]['admissions']),
                    objects_equal=samples[0]['objects']==samples[1]['objects'],admissions_equal=samples[0]['admissions']==samples[1]['admissions'],
                    samples=[{k:v for k,v in s.items() if k not in ('objects','admissions')} for s in samples]))
module.EventLibraryRepository.published_events=fast
print(json.dumps(out))
