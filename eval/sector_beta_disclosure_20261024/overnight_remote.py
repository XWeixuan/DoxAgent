"""Probe the exact missing window, including IBKR's explicit OVERNIGHT venue."""
import json,time
from datetime import datetime,UTC
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
targets=[dict(number=16,symbol='MU',at='20261009 06:06:00 UTC',night_end='20261009 08:00:00 UTC'),dict(number=16,symbol='SOXX',at='20261009 06:06:00 UTC',night_end='20261009 08:00:00 UTC'),dict(number=9,symbol='INTC',at='20261020 03:18:00 UTC',night_end='20261020 08:00:00 UTC'),dict(number=9,symbol='SOXX',at='20261020 03:18:00 UTC',night_end='20261020 08:00:00 UTC'),dict(number=10,symbol='MU',at='20261020 02:01:00 UTC',night_end='20261020 08:00:00 UTC'),dict(number=10,symbol='SOXX',at='20261020 02:01:00 UTC',night_end='20261020 08:00:00 UTC')]
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241054,timeout_seconds=20,market_data_type=1)) as session:
    contracts={s:session.resolve_stock(s)[0] for s in ['INTC','MU','SOXX']}
    emit('connection',captured_at=datetime.now(UTC).isoformat(),server_version=session._app.serverVersion())
    for t in targets:
        try:
            result=session.historical_ticks(contracts[t['symbol']],start_datetime=t['at'],number_of_ticks=20,what_to_show='TRADES',use_rth=False)
            emit('smart_ticks',target=t,response=result)
        except Exception as exc:emit('smart_ticks',target=t,error=type(exc).__name__+': '+str(exc))
        time.sleep(.5)
    done=set()
    for t in targets:
        key=(t['symbol'],t['night_end'])
        if key in done:continue
        done.add(key)
        try:
            result=session.historical_bars(contracts[t['symbol']],duration='1 D',bar_size='1 min',what_to_show='TRADES',use_rth=False,end_datetime=t['night_end'],market_venue='OVERNIGHT',format_date=2)
            emit('overnight_bars',target=t,venue='OVERNIGHT',response=result)
        except Exception as exc:emit('overnight_bars',target=t,venue='OVERNIGHT',error=type(exc).__name__+': '+str(exc))
        time.sleep(.5)
