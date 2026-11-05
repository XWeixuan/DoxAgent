"""Bounded single-writer acceptance. LIVE: exactly one BUY ONDS 1, no exits or retries."""
import os,json,time,threading,sys
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime,timezone
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.worker import WriterLock
from doxagent.trade_execution.strategy import price_and_quantity,valid_quote_side
from doxagent.trade_execution.sessions import Sessions

mode=sys.argv[1];assert mode in {'PAPER','LIVE'}
root=Path('/acceptance')/mode.lower();root.mkdir(mode=0o700,exist_ok=True)
assert not (root/'result.json').exists(),'Completed run cannot submit again'
p=os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3')
journal=RuntimeJournal(p,initialize=False);repo=ExecutionRepository(journal,initialize=False)
revision=journal.get('execution_account_profiles',mode);profile=repo.profile(revision)
assert profile.environment==mode
if mode=='LIVE':
 assert profile.account_type=='CASH'
 paper=json.loads(Path('/acceptance/paper/result.json').read_text());assert paper['closed'] and paper['orders_submitted']==2
state_lock=threading.Lock();events=[];notify=threading.Event();submit_count=[0];active_attempt=[None]

def atomic(path,value):
 tmp=path.with_suffix('.tmp')
 with tmp.open('w') as f:
  os.fchmod(f.fileno(),0o600);json.dump(value,f,default=str,indent=2);f.flush();os.fsync(f.fileno())
 tmp.replace(path)

def sink(kind,value):
 with state_lock:
  events.append((kind,dict(value)))
  with (root/'events.jsonl').open('a') as f:
   os.fchmod(f.fileno(),0o600);f.write(json.dumps(dict(kind=kind,value=value),default=str)+'\n');f.flush();os.fsync(f.fileno())
 notify.set()

def own_fills(attempt):
 families={}
 with state_lock:values=list(events)
 for kind,f in values:
  if kind!='fill' or f.get('order_ref')!=attempt['order_ref']:continue
  family,_,revision=f['exec_id'].rpartition('.');rev=int(revision or 0)
  if family not in families or rev>families[family][0]:families[family]=(rev,f)
 return [v[1] for v in families.values() if D(v[1]['quantity'])>0]

with WriterLock(Path(p)) as writer:
 broker=IbkrSession(profile,event_sink=sink,write_guard=writer.assert_owned)
 data=None
 try:
  snapshot=broker.sync();assert snapshot['complete']
  contract=broker.contract('ONDS','SMART');assert contract['symbol']=='ONDS' and contract['currency']=='USD'
  conid=contract['con_id'];initial=D(snapshot['positions'].get(conid,snapshot['positions'].get(str(conid),'0')))
  atomic(root/'before.json',dict(environment=mode,account=profile.expected_account_id,profile_revision=revision,contract=contract,snapshot=snapshot,initial_quantity=str(initial)))
  outcomes=[]
  for side in (['BUY'] if mode=='LIVE' else ['BUY','SELL']):
   assert side=='BUY' or mode=='PAPER'
   assert submit_count[0] < (1 if mode=='LIVE' else 2)
   # Prefer the actual account's fresh quote. PAPER data is an independent read-only fallback.
   try:q=broker.quote(contract,side,strategy=profile.strategy)
   except (ValueError,RuntimeError,TimeoutError):
    if data is None:
     paper_profile=repo.profile(journal.get('execution_account_profiles','PAPER'))
     data=IbkrSession(paper_profile.model_copy(update={'client_id':195261}))
    qc=data.contract('ONDS','SMART');assert qc['con_id']==conid
    q=data.quote(qc,side,strategy=profile.strategy)
   assert valid_quote_side(q,'BUY',5) and valid_quote_side(q,'SELL',5),'Two fresh sides required'
   bid,ask=D(str(q['bid'])),D(str(q['ask']))
   assert bid>0 and ask>=bid and (ask-bid)/((ask+bid)/2)<D('.03')
   now=datetime.now(timezone.utc);session=Sessions(journal).classify(now,contract)
   assert session!='CLOSED'
   params=price_and_quantity(side=side,kind='LMT',retry=False,session=session,quote=q,rules=broker.market_rules(contract),strategy=profile.strategy,remaining_notional=D(950),remaining_qty=1)
   limit=D(str(params['limit_price']));assert limit>0
   if side=='BUY':
    funds=snapshot['account_values'];assert str(funds.get('AccountReady','')).lower()=='true'
    available=D(funds['AvailableFunds']['value'])
    if mode=='LIVE':
     cash=funds['SettledCash'];assert cash['currency']=='USD'
     strategy_cash=CapitalLedger(repo).book(profile.expected_account_id)['cash']
     assert limit+D(3)<=min(D(cash['value']),available,strategy_cash,D(475))
   # All prices/funds/account checks precede the irreversible one-shot claim.
   orderid=broker.next_id
   with repo.transaction(write=False) as db:
    maximum=db.execute('select max(order_id) from te_attempts where account=? and client_id=?',(profile.expected_account_id,profile.client_id)).fetchone()[0] or 0
   orderid=max(orderid,maximum+1);assert 0<=orderid<2147483647
   attempt=dict(account=profile.expected_account_id,client_id=profile.client_id,order_id=orderid,order_ref='DA-ACCEPT-ONDS1-'+mode+'-'+side+'-20261106',contract=contract,side=side,quantity=1,order_type='LMT',limit_price=str(limit),session=session)
   assert valid_quote_side(q,'BUY',5) and valid_quote_side(q,'SELL',5),'Quote aged during preflight'
   claim=root/(side.lower()+'.claim.json')
   fd=os.open(claim,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'w') as f:
    json.dump(dict(state='SUBMITTING_NO_RETRY',attempt=attempt,quote=q,snapshot_at=snapshot['at']),f,default=str);f.flush();os.fsync(f.fileno())
   active_attempt[0]=attempt
   original=broker.app.placeOrder
   def guarded(order_id,c,o):
    writer.assert_owned()
    assert order_id==attempt['order_id'] and c.conId==conid and c.symbol=='ONDS' and c.secType=='STK'
    assert o.action==side and D(o.totalQuantity)==1 and o.orderType=='LMT' and D(str(o.lmtPrice))==limit
    assert o.account==profile.expected_account_id and o.orderRef==attempt['order_ref']
    if mode=='LIVE':assert side=='BUY' and submit_count[0]==0
    submit_count[0]+=1;return original(order_id,c,o)
   broker.app.placeOrder=guarded
   broker.submit(attempt)  # Exactly one call; no retry, no normal trading intent or hedge.
   broker.app.placeOrder=original
   deadline=time.monotonic()+20
   while time.monotonic()<deadline and sum((D(f['quantity']) for f in own_fills(attempt)),D(0))<1:
    notify.wait(min(1,max(0,deadline-time.monotonic())));notify.clear()
   fills=own_fills(attempt)
   if sum((D(f['quantity']) for f in fills),D(0))<1:
    status=broker.orders.get(orderid,{})
    if status.get('status') in {'Submitted','PreSubmitted'}:
     broker.cancel(attempt)  # Remove this one unfilled order, never replace it.
     notify.wait(2);notify.clear()
   # Full explicit snapshot, not order status alone, proves the result.
   snapshot=broker.sync();fills=own_fills(attempt)
   qty=sum((D(f['quantity']) for f in fills),D(0));assert qty<=1
   result=dict(side=side,quantity=str(qty),order_id=orderid,order_ref=attempt['order_ref'],limit=str(limit),fills=fills,broker_status=broker.orders.get(orderid),snapshot_at=snapshot['at'],stages=snapshot['stage_seconds'])
   outcomes.append(result);atomic(root/(side.lower()+'-result.json'),result)
   assert qty==1,'One-shot order did not fully fill; no replacement permitted'
  final_qty=D(snapshot['positions'].get(conid,snapshot['positions'].get(str(conid),'0')))
  assert final_qty==initial+(D(1) if mode=='LIVE' else D(0))
  result=dict(environment=mode,orders_submitted=submit_count[0],initial_quantity=str(initial),final_quantity=str(final_qty),closed=mode=='PAPER',manual_holding=mode=='LIVE',outcomes=outcomes,at=datetime.now(timezone.utc).isoformat())
  atomic(root/'result.json',result)
  print(json.dumps(dict(environment=mode,orders_submitted=submit_count[0],initial_quantity=str(initial),final_quantity=str(final_qty),closed=mode=='PAPER',outcomes=[{k:v[k] for k in ['side','quantity','order_id','order_ref','limit','stages']} for v in outcomes])))
 except BaseException as exc:
  atomic(root/'error.json',dict(error=str(exc),orders_submitted=submit_count[0],active_attempt=active_attempt[0],at=datetime.now(timezone.utc).isoformat()))
  raise
 finally:
  if data:data.close()
  broker.close()
