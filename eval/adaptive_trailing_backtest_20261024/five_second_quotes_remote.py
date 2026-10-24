"""Five-second BID/ASK paths. Close is used only after the bar completes."""
import json,time
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241066,timeout_seconds=40,market_data_type=1)) as s:
    for symbol in ['RKLB','QQQ']:
        c=s.resolve_stock(symbol)[0]
        for show in ['BID','ASK']:
            try:
                r=s.historical_bars(c,duration='2 D',bar_size='5 secs',what_to_show=show,use_rth=False,end_datetime='20261015 19:31:00 UTC',format_date=2)
                emit('five_second_quotes',symbol=symbol,show=show,venue='SMART',response=r)
            except Exception as exc:emit('five_second_quotes',symbol=symbol,show=show,error=type(exc).__name__+': '+str(exc))
            time.sleep(2)
