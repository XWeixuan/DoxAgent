"""Read-only IBKR history for strictly pre-entry intraday Beta estimation."""
import json,time
from datetime import datetime,UTC,timedelta
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
symbols=['BE','COHR','INTC','MU','NBIS','RKLB','SOXX','QQQ','IGV','SKYY','XLK']
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=231051,timeout_seconds=45,market_data_type=1)) as session:
    emit('connection',captured_at=datetime.now(UTC).isoformat(),server_version=session._app.serverVersion())
    for symbol in symbols:
        try:
            choices=session.resolve_stock(symbol);assert len(choices)==1
            contract=choices[0]
            response=session.historical_bars(contract,duration='4 M',bar_size='30 mins',what_to_show='TRADES',use_rth=True,end_datetime='20261022 00:00:00 UTC',format_date=2)
            emit('history',symbol=symbol,contract=contract.as_dict(),captured_at=datetime.now(UTC).isoformat(),response=response)
        except Exception as exc:emit('history',symbol=symbol,error=type(exc).__name__+': '+str(exc))
        time.sleep(.5)
