import json,time
from datetime import datetime,timedelta,UTC
from importlib.metadata import version
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
plan=__PLAN__
def emit(kind,**kw): print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=221042,timeout_seconds=25,market_data_type=1)) as session:
    contracts={}
    for symbol in sorted(set(t['ticker'] for t in plan['targets'])):
        choices=session.resolve_stock(symbol)
        assert len(choices)==1,(symbol,[c.as_dict() for c in choices])
        contracts[symbol]=choices[0]
    emit('connection',captured_at=datetime.now(UTC).isoformat(),server_version=session._app.serverVersion(),ibapi_version=version('ibapi'),contracts={s:c.as_dict() for s,c in contracts.items()})
    for index,t in enumerate(plan['targets'],1):
        at=datetime.fromisoformat(t['at']);q=at.replace(microsecond=0)+(timedelta(seconds=1) if at.microsecond else timedelta())
        try:
            response=session.historical_ticks(contracts[t['ticker']],start_datetime=q.strftime('%Y%m%d %H:%M:%S UTC'),number_of_ticks=40,what_to_show='TRADES',use_rth=t['kind']=='exit')
            emit('query',index=index,target=t,query_start=q.isoformat(),captured_at=datetime.now(UTC).isoformat(),response=response)
        except Exception as exc: emit('query',index=index,target=t,error=type(exc).__name__+': '+str(exc))
        time.sleep(.4)
