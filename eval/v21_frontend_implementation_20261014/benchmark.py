import importlib.util, json, sqlite3, time, os, sys
from pathlib import Path
from datetime import UTC, datetime
from doxagent.v2_read.repository import ReadStore

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,file); module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
candidate=load('doxagent.v2_read.repository_candidate','/tmp/ui-impl/repository.py')
queries=load('doxagent.api_v2.policy_queries','/tmp/ui-impl/policy_queries.py')
source=ReadStore('/data/read/v2.sqlite3')
target=Path('/tmp/ui-impl/isolated-final.sqlite3')
if target.exists(): raise RuntimeError('Refusing to overwrite validation database')
store=candidate.ReadStore(target)
with sqlite3.connect(target) as dest:
    dest.execute("ATTACH DATABASE 'file:/data/read/v2.sqlite3?mode=ro' AS origin")
    seq=int(dest.execute("SELECT value FROM origin.read_meta WHERE key='highwater'").fetchone()[0])
    for table, predicate in [('objects',"kind IN ('policy_catalog','policy_change')"),('contributions',"metric IN ('policy_ar_hits','policy_executed')")]:
        ddl=dest.execute("SELECT sql FROM origin.sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
        dest.execute(ddl)
        began=time.perf_counter()
        columns=",".join(r[1] for r in dest.execute("PRAGMA origin.table_info("+table+")"))
        dest.execute("INSERT INTO "+table+" ("+columns+") SELECT "+columns+" FROM origin."+table+" WHERE "+predicate+" AND ticker IN ('MU','BE','INTC') AND valid_from<=?",(seq,))
        dest.commit()
        print('copied',table,dest.execute('SELECT count(*) FROM '+table).fetchone()[0],round(time.perf_counter()-began,2),flush=True)
store.migrate()
with sqlite3.connect(target) as dest:
    dest.execute("UPDATE read_meta SET value=? WHERE key='highwater'",(str(seq),))
    # Source-Shell fixture is read-only test metadata, inserted without ingesting a commit.
    for ticker in ['BE','MU','INTC']:
        dest.execute("INSERT INTO objects(kind,ticker,id,payload,valid_from,sort_key) VALUES('shell',?,'fixture-d2:shell-0001','{\"shell_id\":\"shell-0001\"}',0,'fixture')",(ticker,))
start=time.perf_counter();store.migrate_policy_query_indexes();build_ms=(time.perf_counter()-start)*1000
print('indexes_ms',round(build_ms,1),'bytes',target.stat().st_size,flush=True)
results=[]
periods={'CURRENT_TRADING_DAY':['2026-10-13'],'PREVIOUS_TRADING_DAY':['2026-10-12'], 'TRADING_DAYS_7':['2026-10-13','2026-10-12','2026-10-09','2026-10-08','2026-10-07','2026-10-06','2026-10-05'], 'TRADING_DAYS_30': [(datetime(2026,10,13)-__import__('datetime').timedelta(days=i)).date().isoformat() for i in range(60) if (datetime(2026,10,13)-__import__('datetime').timedelta(days=i)).weekday()<5][:30],'ALL':None}
for ticker in ['BE','MU','INTC']:
 for period,days in periods.items():
  for watermark in [seq,seq-1]:
   for shell in ['ALL','shell-0001']:
    samples=[]
    for _ in range(5):
     t=time.perf_counter();result=queries.metrics(store,ticker,watermark,shell,days);samples.append(round((time.perf_counter()-t)*1000,2))
    results.append(dict(ticker=ticker,period=period,seq=watermark,shell=shell,ms=samples,values=result))
    print(ticker,period,'current' if watermark==seq else 'frozen',shell,'max_ms',max(samples),flush=True)
with store.connect() as db:
 sql,args=queries.selection('INTC',seq-1,'ALL',None,db=db)
 plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,args)]
# Exercise the real response assembly/middleware on this isolated copy, with synthetic auth/view tokens.
from doxagent.api_v2.auth import Principal
from doxagent.api_v2.errors import ApiFailure
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from fastapi.testclient import TestClient
import doxagent.api_v2.app as api_module
api_module.policies=load('doxagent.api_v2.policies_candidate','/tmp/ui-impl/policies.py')
class Auth:
 def configuration(self):return {'provider':'supabase','supabase_url':'https://test.supabase.co','supabase_publishable_key':'sb_publishable_fixture'}
 async def authenticate(self,token):
  if token!='offline':raise ApiFailure('UNAUTHORIZED',401)
  return Principal('developer','DEVELOPER',time.time()+3600)
control=ControlRepository(RuntimeJournal('/tmp/ui-impl/runtime-test.sqlite3'));control.migrate()
app=api_module.create_app(store=store,control=control,auth=Auth())
api_results=[]
with TestClient(app) as client:
 for ticker in ['BE','MU','INTC']:
  for period,days in periods.items():
   for watermark in [seq,seq-1]:
    for shell in ['ALL','shell-0001']:
     # Supply an indexed source Shell only for validating the existing scope guard.
     view=store.save_token('developer','fixture',{'seq':watermark,'as_of':datetime.now(UTC).isoformat().replace('+00:00','Z'), 'wire':{'ticker':ticker,'page':'POLICIES','activation':{'data':{'document2':{'run_id':'fixture-d2'}}},'period':{'selected':period,'current':{'trading_days':days or []},'previous':None}}},view=True)
     # The guard's source Shell must exist at the same old view; ALL needs no Shell row.
     samples=[]; statuses=[]
     for _ in range(5):
      t=time.perf_counter();r=client.get('/api/doxagent/v2/tickers/'+ticker+'/policies/metrics',params={'view_id':view,'shell_id':shell},headers={'Authorization':'Bearer offline'});samples.append(round((time.perf_counter()-t)*1000,2));statuses.append(r.status_code)
     api_results.append(dict(ticker=ticker,period=period,seq=watermark,shell=shell,ms=samples,statuses=statuses,error=r.text[:200] if r.status_code!=200 else None))
Path('/tmp/ui-impl/results-bulk.json').write_text(json.dumps(dict(index_build_ms=build_ms,bytes=target.stat().st_size,sql=results,api=api_results,plan=plan),ensure_ascii=True))
print('BENCHMARK_COMPLETE',flush=True)
