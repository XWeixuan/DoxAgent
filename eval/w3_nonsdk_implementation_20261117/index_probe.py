import sqlite3,json,time,hashlib,resource
from pathlib import Path
from doxagent.v2_read.native_content import NativeContent
from doxagent.persistent_runtime_v2.daily_index import event_provider,fact_content,fact_candidate
p=Path('/data/runtime/runtime.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.1)
v=json.loads(d.execute('select payload_json from runtime_v2_cases where case_id=?',('case_4f4cf41c22f54ba5b436715ad0592752',)).fetchone()[0]);d.close()
f=NativeContent(p).decode(v['frozen_inputs']);pin=f['w1_daily_index'];root=v['version_pin']['event_library_root'];version=v['version_pin']['event_library_version'];old=event_provider(None,pin['event_library_root']);now=event_provider(None,root)
seen={fact_content(fact_candidate(e,a)) for e,a in old.published_facts('INTC',pin['event_library_version'])}
from doxagent.persistent_runtime_v2.schema import RuntimeFactCandidate
seen|={fact_content(RuntimeFactCandidate.model_validate(a['candidate'])) for a in f['provisional']}
r=now.reader._repository('INTC')
from doxagent.event_library.compiler import EventLibraryViewCompiler,_event_order,_wire_cell,_display_occurrence_time
c=time.process_time();a=time.perf_counter()
expected=''.join(' | '.join([e.event_id,_wire_cell(_display_occurrence_time(e.occurred_at)),_wire_cell(e.known_event_summary)])+'\n' for e in _event_order(r.published_events('INTC',version)))
baseline=dict(wall_ms=(time.perf_counter()-a)*1000,cpu_ms=(time.process_time()-c)*1000)
c=time.process_time();a=time.perf_counter();actual=EventLibraryViewCompiler(r).known_event_index('INTC',version);optimized=dict(wall_ms=(time.perf_counter()-a)*1000,cpu_ms=(time.process_time()-c)*1000)
print(json.dumps(dict(baseline=baseline,optimized=optimized,equal=expected==actual,index_chars=len(actual),rows=len(actual.splitlines()),sdk_calls=0)))