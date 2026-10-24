"""Full BBO including size-only refresh around the earliest candidate exit."""
import json,time
from datetime import datetime,UTC,timedelta
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig,IbkrTwsSession
def emit(**kw):print(json.dumps(kw),flush=True)
target=datetime(2026,10,14,20,54,6,tzinfo=UTC)
cursors={s:datetime(2026,10,14,20,53,20,tzinfo=UTC) for s in ['RKLB','QQQ']}
with IbkrTwsSession(IbkrTwsConfig(host='host.docker.internal',port=7496,client_id=241070,timeout_seconds=30,market_data_type=1)) as sess:
    contracts={s:sess.resolve_stock(s)[0] for s in cursors}
    for i in range(12):
        remaining=[s for s,t in cursors.items() if t<target]
        if not remaining:break
        s=min(remaining,key=lambda s:cursors[s]);at=cursors[s];before=time.monotonic()
        try:
            response=sess.historical_ticks(contracts[s],start_datetime=at.strftime('%Y%m%d %H:%M:%S UTC'),number_of_ticks=1000,what_to_show='BID_ASK',use_rth=False)
            emit(kind='full_confirmation_page',symbol=s,requested_at=at.isoformat(),response=response,ignore_size=False)
            end=datetime.fromisoformat(response['events'][-1]['timestamp'])
            if end<at:raise RuntimeError('no forward progress')
            cursors[s]=end+timedelta(seconds=1)
        except Exception as exc:emit(kind='confirmation_error',symbol=s,error=str(exc));break
        if any(t<target for t in cursors.values()):time.sleep(max(0,25.5-(time.monotonic()-before)))
