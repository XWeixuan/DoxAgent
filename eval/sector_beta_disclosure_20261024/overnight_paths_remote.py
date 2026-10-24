"""Fill every overnight holding path, beyond the three entry probes."""
import json,time
from datetime import datetime,UTC
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
targets=[('BE','20261007 08:00:00 UTC'),('SOXX','20261007 08:00:00 UTC'),('RKLB','20261015 08:00:00 UTC'),('QQQ','20261015 08:00:00 UTC'),('NBIS','20261021 08:00:00 UTC'),('QQQ','20261021 08:00:00 UTC'),('MU','20261021 08:00:00 UTC'),('SOXX','20261021 08:00:00 UTC')]
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241055,timeout_seconds=25,market_data_type=1)) as s:
    contracts={symbol:s.resolve_stock(symbol)[0] for symbol in sorted({t[0] for t in targets})}
    emit('connection',captured_at=datetime.now(UTC).isoformat(),server_version=s._app.serverVersion())
    for symbol,end in targets:
        target=dict(symbol=symbol,night_end=end)
        try:
            result=s.historical_bars(contracts[symbol],duration='1 D',bar_size='1 min',what_to_show='TRADES',use_rth=False,end_datetime=end,market_venue='OVERNIGHT',format_date=2)
            emit('overnight_bars',target=target,venue='OVERNIGHT',response=result)
        except Exception as exc:emit('overnight_bars',target=target,error=type(exc).__name__+': '+str(exc))
        time.sleep(.4)
