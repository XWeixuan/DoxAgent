import json,time
from datetime import datetime,UTC,timedelta
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
cursors={s:datetime.fromisoformat(t) for s,t in __CURSORS__.items()};cached=__CACHE__;requests=__REQUESTS__
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241064,timeout_seconds=30,market_data_type=1)) as s:
    app=s._app;original=app.reqHistoricalTicks
    def prices(req_id,contract,start,end,n,show,rth,ignore,options):return original(req_id,contract,start,end,n,show,rth,True,options)
    app.reqHistoricalTicks=prices
    contracts={name:s.resolve_stock(name)[0] for name in cursors}
    emit('page_session',captured_at=datetime.now(UTC).isoformat(),cursors={k:v.isoformat() for k,v in cursors.items()},ignore_size=True)
    for i in range(requests):
        symbol=sorted(cursors,key=lambda k:cursors[k])[0];at=cursors[symbol]
        usable=next((q for q in cached if q['symbol']==symbol and abs((datetime.strptime(q['at'],'%Y%m%d %H:%M:%S UTC').replace(tzinfo=UTC)-at).total_seconds())<=1),None)
        if usable:
            end=datetime.fromisoformat(usable['response']['events'][-1]['timestamp']);cursors[symbol]=end+timedelta(seconds=1);cached.remove(usable);continue
        before=time.monotonic()
        try:
            result=s.historical_ticks(contracts[symbol],start_datetime=at.strftime('%Y%m%d %H:%M:%S UTC'),number_of_ticks=1000,what_to_show='BID_ASK',use_rth=False)
            end=datetime.fromisoformat(result['events'][-1]['timestamp'])
            assert end>=at,(symbol,at,end)
            emit('quote_page',symbol=symbol,requested_at=at.isoformat(),captured_at=datetime.now(UTC).isoformat(),response=result,ignore_size=True)
            cursors[symbol]=end+timedelta(seconds=1)
        except Exception as exc:
            emit('quote_error',symbol=symbol,requested_at=at.isoformat(),error=type(exc).__name__+': '+str(exc));time.sleep(40)
        elapsed=time.monotonic()-before
        if i+1<requests:time.sleep(max(0,25.5-elapsed))
