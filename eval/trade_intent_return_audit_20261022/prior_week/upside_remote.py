"""Read-only minute TRADES for intent-day upside and a simple SPY comparison."""
import json,time
from datetime import datetime,timedelta,UTC,time as wall
from zoneinfo import ZoneInfo
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
targets=[{'ticker': 'BE', 'day': '2026-10-07'}, {'ticker': 'INTC', 'day': '2026-10-06'}, {'ticker': 'MU', 'day': '2026-10-09'}, {'ticker': 'SPY', 'day': '2026-10-06'}, {'ticker': 'SPY', 'day': '2026-10-07'}, {'ticker': 'SPY', 'day': '2026-10-09'}]
et=ZoneInfo('America/New_York')
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=221043,timeout_seconds=40,market_data_type=1)) as session:
    contracts={}
    for symbol in sorted({t['ticker'] for t in targets}):
        choices=session.resolve_stock(symbol);assert len(choices)==1
        contracts[symbol]=choices[0]
    emit('connection',captured_at=datetime.now(UTC).isoformat(),server_version=session._app.serverVersion(),contracts={s:c.as_dict() for s,c in contracts.items()})
    for n,t in enumerate(targets,1):
        end=datetime.combine(datetime.fromisoformat(t['day']).date(),wall(20),et).astimezone(UTC)
        try:
            response=session.historical_bars(contracts[t['ticker']],duration='1 D',bar_size='1 min',what_to_show='TRADES',use_rth=False,end_datetime=end.strftime('%Y%m%d %H:%M:%S UTC'),format_date=2)
            emit('bars',index=n,target=t,end_utc=end.isoformat(),captured_at=datetime.now(UTC).isoformat(),response=response)
        except Exception as exc:emit('bars',index=n,target=t,error=type(exc).__name__+': '+str(exc))
        time.sleep(.4)
