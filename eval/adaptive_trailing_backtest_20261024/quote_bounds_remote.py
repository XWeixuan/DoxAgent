"""One-minute quote bounds can disprove activation, never simulate a trigger."""
import json,time
from datetime import datetime,UTC
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241063,timeout_seconds=25,market_data_type=1)) as s:
    for symbol,show in [('INTC','ASK'),('SOXX','BID')]:
        try:
            c=s.resolve_stock(symbol)[0];r=s.historical_bars(c,duration='1 D',bar_size='1 min',what_to_show=show,use_rth=False,end_datetime='20261014 19:31:00 UTC',format_date=2)
            emit('quote_bounds',symbol=symbol,show=show,response=r)
        except Exception as exc:emit('quote_bounds',symbol=symbol,error=type(exc).__name__+': '+str(exc))
    for symbol in ['INTC','SOXX','RKLB','QQQ']:
        at='20261014 19:29:55 UTC' if symbol in ['INTC','SOXX'] else '20261015 19:29:55 UTC'
        try:
            c=s.resolve_stock(symbol)[0];r=s.historical_ticks(c,start_datetime=at,number_of_ticks=100,what_to_show='BID_ASK',use_rth=False)
            emit('fixed_exit_quotes',symbol=symbol,at=at,response=r)
        except Exception as exc:emit('fixed_exit_quotes',symbol=symbol,error=type(exc).__name__+': '+str(exc))
        time.sleep(.6)
