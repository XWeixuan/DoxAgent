"""Hourly host-owned compensation using current activation; old receipts stay immutable."""
import json,sys,os
from pathlib import Path
from datetime import datetime,UTC
from doxagent.settings import DoxAgentSettings
from doxagent.semantic_clock import semantic_day
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
ROOT=Path(os.environ.get('DOXAGENT_O2_BACKFILL_ROOT','/data/runtime/o2-recovery-20261101'))
TICKERS=('MU','INTC','BE','RKLB','COHR','NBIS','ONDS','CEG','SNOW','IONQ')
j=RuntimeJournal(DoxAgentSettings().persistent_runtime_v2_sqlite_path,initialize=False)
def emit(event,**fields):print(json.dumps({'at':datetime.now(UTC).isoformat(),'event':event,**fields}),flush=True)
def snapshot(ticker,end):
 ts=j.tasks(ticker=ticker,kind='MAINTENANCE')
 seen={(x['source_message_id'],x['candidate_index']) for t in ts if t['status']=='SUCCEEDED' for x in t['receipt'].get('frame',{}).get('candidates',[])}
 with j.transaction(write=False) as db:
  rows=[dict(r) for r in db.execute("SELECT x.source_message_id,x.candidate_index,x.trading_date,x.daily_status,c.case_id FROM runtime_v2_candidates x LEFT JOIN runtime_v2_cases c ON c.source_message_id=x.source_message_id AND c.ticker=x.ticker WHERE x.ticker=? AND x.trading_date<? AND x.daily_status='PENDING' AND NOT EXISTS(SELECT 1 FROM runtime_v2_admission_exclusions a WHERE a.source_message_id=x.source_message_id) AND NOT EXISTS(SELECT 1 FROM runtime_values v WHERE v.namespace='invalid_admissions' AND v.key=x.source_message_id AND v.payload!='false')",(ticker,end))]
 return ts,[r for r in rows if (r['source_message_id'],r['candidate_index']) not in seen]
def plan():
 end=str(semantic_day(datetime.now(UTC)))
 result={'created_at':datetime.now(UTC).isoformat(),'exclusive_day':end,'tickers':{}}
 for ticker in TICKERS:
  ts,rows=snapshot(ticker,end);days={}
  for r in rows:
   x=days.setdefault(r['trading_date'],{'case_ids':set(),'uncovered':[],'failed_tasks':[]})
   if not r['case_id']:raise ValueError('Missing Case for '+r['source_message_id'])
   x['case_ids'].add(r['case_id']);x['uncovered'].append([r['source_message_id'],r['candidate_index']])
  for t in ts:
   day=t['inputs'].get('day','9999')
   if t['status']!='FAILED' or day>=end:continue
   x=days.setdefault(day,{'case_ids':set(),'uncovered':[],'failed_tasks':[]})
   x['failed_tasks'].append(t['id'])
   ids=t['receipt'].get('frame',{}).get('case_ids',[]) or t['inputs'].get('case_ids',[])
   if not ids:
    from doxagent.persistent_runtime_v2.bounded_inputs import predicate
    where,args=predicate(ticker,t['inputs'])
    with j.transaction(write=False) as db:ids=[r[0] for r in db.execute('SELECT c.case_id FROM runtime_v2_cases c WHERE '+where,args)]
   x['case_ids'].update(ids)
  for x in days.values():x['case_ids']=sorted(x['case_ids'])
  result['tickers'][ticker]={'days':dict(sorted(days.items()))}
 return result
if '--inspect' in sys.argv:
 print(json.dumps(plan(),indent=2));sys.exit()
ROOT.mkdir(parents=True,exist_ok=True)
if (ROOT/'done.json').exists():sys.exit()
f=ROOT/'plan.json'
if not f.exists():f.write_text(json.dumps(plan(),indent=2))
p=json.loads(f.read_text());done=True;acceptance={}
for ticker,e in p['tickers'].items():
 try:
  ts,uncovered=snapshot(ticker,p['exclusive_day'])
  active=[t for t in ts if t['status'] in ('PENDING','RUNNING','HELD')]
  if active:
   emit('waiting_active',ticker=ticker,tasks=[{'id':t['id'],'status':t['status']} for t in active]);done=False;continue
  pending=False
  for day,x in e['days'].items():
   ident=f'manual:o2-backfill:{ticker}:{day}:20261101'
   prior=j.get_task(ident)
   if prior and prior['status']=='SUCCEEDED':continue
   done=False;pending=True
   if prior:
    # A settled local failure remains visible but cannot hold later dates.
    # The repaired release gets a separate bounded operator recovery budget.
    n=j.get('operator_o2_recovery_20261101_r2',ident,0)
    if prior['status']=='FAILED' and n<2:
     j.resume(ident,'O2 frozen-view and selected-version export repair 2026-11-01')
     j.set('operator_o2_recovery_20261101_r2',ident,n+1)
     emit('resumed',ticker=ticker,task=ident)
     break
    emit('isolated_failure',ticker=ticker,task=ident,status=prior['status'],attempts=n)
    continue
   c=ControlRepository(j);c.admit(ident,ticker);epoch=c.get(ticker)
   j.put_task(ident,ticker,'MAINTENANCE',{'scope':'SUPPLEMENTAL','day':day,'cutoff':datetime.now(UTC).isoformat(),'case_ids':x['case_ids'],'control_epoch':epoch['epoch'] if epoch else None,'manual_reason':'User authorized chronological provisional compensation; old failed tasks immutable','compensates':x['failed_tasks']})
   emit('scheduled',ticker=ticker,day=day,task=ident,uncovered_candidates=len(x['uncovered']),old_failures=x['failed_tasks']);break
  if pending:continue
  targets={tuple(v) for x in e['days'].values() for v in x['uncovered']}
  remaining=[r for r in uncovered if (r['source_message_id'],r['candidate_index']) in targets]
  if remaining:
   emit('needs_attention',ticker=ticker,reason='uncovered_after_success',count=len(remaining));done=False;continue
  owned=[t for t in ts if t['id'].startswith('manual:o2-backfill:'+ticker+':') and t['id'].endswith(':20261101')]
  resolved={key for t in owned for key in t['receipt'].get('resolved_candidates',[])}
  with j.transaction(write=False) as db:
   rows=db.execute("SELECT source_message_id,candidate_index,daily_status FROM runtime_v2_candidates WHERE ticker=? AND trading_date IN (SELECT key FROM json_each(?))",(ticker,json.dumps(e['days']))).fetchall()
  count=sum(r['daily_status']=='PROCESSED' and f"runtime-v2-msg:{r['source_message_id']}:{r['candidate_index']}" in resolved for r in rows)
  acceptance[ticker]={'days':list(e['days']),'tasks':len(owned),'processed_unique':count,'uncovered_eligible_target_pending':0,'superseded_old_failed_tasks':[k for x in e['days'].values() for k in x['failed_tasks']]}
  emit('ticker_complete',ticker=ticker,processed_unique=count)
 except Exception as exc:
  done=False;emit('tick_error',ticker=ticker,error_type=type(exc).__name__,message=str(exc)[:800])
if done:
 receipt={'completed_at':datetime.now(UTC).isoformat(),'tickers':acceptance}
 (ROOT/'acceptance.json').write_text(json.dumps(receipt,indent=2));(ROOT/'done.json').write_text(json.dumps(receipt,indent=2));emit('complete',receipt=receipt)
