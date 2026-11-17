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
a=time.perf_counter();c=time.process_time();admissions=r.fact_admissions('INTC',version);found={}
for e in r.published_events('INTC',version):
 for fact in e.facts:
  at=admissions[fact.fact_id][0]
  if at>pin['event_library_version'] and fact_content(fact_candidate(e,fact)) not in seen:found.setdefault(fact.fact_id,(at,e,fact))
reference=sorted(found.values(),key=lambda x:(x[0],int(x[2].fact_id[1:])));baseline=dict(wall_ms=(time.perf_counter()-a)*1000,cpu_ms=(time.process_time()-c)*1000)
a=time.perf_counter();c=time.process_time();fast=now.new_library_facts('INTC',pin['event_library_version'],version,excluded_contents=seen);optimized=dict(wall_ms=(time.perf_counter()-a)*1000,cpu_ms=(time.process_time()-c)*1000)
def identity(xs):return [(a,e.event_id,f.fact_id,fact_content(fact_candidate(e,f))) for a,e,f in xs]
print(json.dumps(dict(baseline=baseline,optimized=optimized,equal=identity(reference)==identity(fast),eligible_facts=len(fast),seen_contents=len(seen),frozen_version=pin['event_library_version'],current_version=version,maxrss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)))
