import importlib.util,json,sys,time,math
spec=importlib.util.spec_from_file_location('doxagent.api_v2.policy_queries','/tmp/ui-impl/policy_queries.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.query_budget import deadline
s=ReadStore('/tmp/ui-impl/isolated-final.sqlite3')
j=json.load(open('/tmp/ui-impl/acceptance-results.json'))
# Keep every original sample and add 20 to the one scope with a >500ms outlier.
extras=[]
for row in j['sql']:
 if max(row['ms'])<=500:continue
 samples=[]
 for _ in range(20):
  token=deadline.set(time.monotonic()+3);t=time.perf_counter()
  try:result=q.metrics(s,row['ticker'],row['seq'],row['shell'],None)
  finally:deadline.reset(token)
  assert result==row['values']
  samples.append(round((time.perf_counter()-t)*1000,2))
 extra={k:v for k,v in row.items() if k not in ['values','ms']};extra['ms']=samples;extras.append(extra)
 combined=sorted(row['ms']+samples)
 print(row['ticker'],row['period'],'n',len(combined),'p95',combined[math.ceil(.95*len(combined))-1],'max',max(combined),flush=True)
open('/tmp/ui-impl/extra-samples.json','w').write(json.dumps(extras))
