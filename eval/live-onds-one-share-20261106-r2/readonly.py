import importlib.util,json,os,sqlite3,concurrent.futures,time
import doxagent.trade_execution.ibkr_session as module
from doxagent.trade_execution.schema import historical_profile
p=os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3');db=sqlite3.connect('file:'+p+'?mode=ro',uri=True);profiles={}
for index,env in enumerate(['LIVE','PAPER']):
 revision=json.loads(db.execute("select payload from runtime_values where namespace='execution_account_profiles' and key=?",(env,)).fetchone()[0]);profiles[env]=historical_profile(json.loads(db.execute('select payload from te_profiles where revision=?',(revision,)).fetchone()[0])).model_copy(update={'client_id':195291+index})
db.close()
def probe(item):
 env,profile=item;events=[];broker=module.IbkrSession(profile,event_sink=lambda k,v:events.append((k,v)),write_guard=lambda:(_ for _ in ()).throw(AssertionError('READONLY')))
 try:
  started=time.monotonic();value=broker.sync();assert value['complete'] and value['history_complete']
  return dict(environment=env,complete=value['complete'],elapsed=round(time.monotonic()-started,3),at=value['at'],stage_seconds=value['stage_seconds'],orders_submitted=0,completed_end=broker.completed_event.is_set(),history_complete=value['history_complete'],snapshot_positions_count=len(value['positions']),snapshot_orders_count=len(broker.orders))
 finally:broker.close()
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:print(json.dumps(list(pool.map(probe,profiles.items()))))
