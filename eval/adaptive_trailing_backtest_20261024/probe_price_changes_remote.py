import json,time
from datetime import datetime,UTC
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241062,timeout_seconds=25,market_data_type=1)) as s:
    app=s._app;original=app.reqHistoricalTicks
    def price_changes(req_id,contract,start,end,n,show,rth,ignore,options):return original(req_id,contract,start,end,n,show,rth,True,options)
    app.reqHistoricalTicks=price_changes
    for symbol,at in [('RKLB','20261014 20:40:00 UTC'),('QQQ','20261014 20:40:00 UTC')]:
        try:
            c=s.resolve_stock(symbol)[0];r=s.historical_ticks(c,start_datetime=at,number_of_ticks=1000,what_to_show='BID_ASK',use_rth=False)
            emit('price_change_probe',symbol=symbol,at=at,response=r,ignore_size=True)
        except Exception as exc:emit('price_change_probe',symbol=symbol,error=type(exc).__name__+': '+str(exc))
        time.sleep(.5)
