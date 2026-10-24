"""Probe historical executable quotes before committing to a tick replay."""
import json,time,sqlite3
from datetime import datetime,UTC
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
p='/data/runtime/runtime.sqlite3';db=sqlite3.connect('file:'+p+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
ids=['trade:case_c5e73efb13bc4bf9b262e198fd4227cf','trade:case_cf7cc11b11df4d298b1d15b94864196e']
for ident in ids:
    er=db.execute('SELECT payload FROM te_executions WHERE id=?',(ident,)).fetchone()
    fills=[]
    for raw in db.execute('SELECT payload FROM te_fills'):
        v=json.loads(raw[0])
        if str(v.get('attempt_id','')).startswith(ident+':entry:'):fills.append(v)
    emit('ledger',intent_id=ident,execution=json.loads(er[0]) if er else None,entry_fills=fills)
db.close()
targets=[('INTC','20261014 11:30:00 UTC'),('SOXX','20261014 11:30:00 UTC'),('RKLB','20261014 20:40:00 UTC'),('QQQ','20261014 20:40:00 UTC')]
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241061,timeout_seconds=25,market_data_type=1)) as s:
    emit('connection',server_version=s._app.serverVersion(),captured_at=datetime.now(UTC).isoformat())
    for symbol,at in targets:
        try:
            c=s.resolve_stock(symbol)[0]
            result=s.historical_ticks(c,start_datetime=at,number_of_ticks=1000,what_to_show='BID_ASK',use_rth=False)
            emit('bid_ask_probe',symbol=symbol,at=at,contract=c.as_dict(),response=result)
        except Exception as exc:emit('bid_ask_probe',symbol=symbol,at=at,error=type(exc).__name__+': '+str(exc))
        time.sleep(.5)
