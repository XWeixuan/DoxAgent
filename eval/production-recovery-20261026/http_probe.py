"""Exercise the deployed API query-worker routes; no production auth changes."""
import asyncio
import json
import time
from doxagent.api_v2.auth import Principal
from doxagent.api_v2.query_runner import QueryRunner
from doxagent.api_v2.errors import ApiFailure
from doxagent.api_v2.views import Views
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.settings import Limits


async def main():
    store=ReadStore('/data/read/active.json')
    views=Views(store,PageCalendar())
    owner='production-recovery-verifier'
    limits=Limits.load()
    runner=QueryRunner(workers=limits.query_workers,queue_limit=limits.query_queue,
        name='recovery-probe',queue_seconds=limits.queue_seconds,cancel_grace=1)
    await runner.start()
    results=[]
    async def call(ticker,route):
        start=time.monotonic()
        try:
            status,_,body=await runner.run(dict(kind='http',url='/api/doxagent/v2'+route,
                principal=Principal(owner,'DEVELOPER',time.time()+300),headers={},route='recovery-probe'),timeout=3)
            value=json.loads(body)
            data=value.get('data') or {}
            if isinstance(data,dict) and data.get('state') in {'AVAILABLE','EMPTY'}:data=data.get('data') or {}
            if 'messages' in data:data=data['messages']
            summary={k:data.get(k) for k in ('has_more','source_counts','latency_sample_count') if k in data}
            if 'items' in data:
                summary['items']=len(data['items'])
                if data['items'] and 'stream_published_at' in data['items'][0]:
                    summary['latest']=data['items'][0]['stream_published_at']
            if 'published_revisions' in data:summary['published']=data['published_revisions'].get('current')
            result=dict(ticker=ticker,route=route.split('?')[0],status=status,
                        seconds=round(time.monotonic()-start,3),error=value.get('error'),summary=summary)
        except ApiFailure as e:
            result=dict(ticker=ticker,route=route.split('?')[0],status=e.status,error=e.code,
                        seconds=round(time.monotonic()-start,3))
        results.append(result);print(json.dumps(result),flush=True)
        return value if result['status']==200 else None
    try:
        for ticker in ['INTC','MU','BE','COHR','NBIS','CEG','RKLB','SNOW','ONDS']:
            context=await call(ticker,'/read-context?page=MESSAGE_BUS&ticker='+ticker+'&period=CURRENT_TRADING_DAY')
            if not context:continue
            view=context['data']['view_id']
            feed=await call(ticker,'/read-context?page=MESSAGE_BUS&ticker='+ticker+'&period=ALL')
            if not feed:continue
            root='/tickers/'+ticker
            routes=[root+'/message-bus/'+name+'?view_id='+view for name in ['sources','status','metrics']]
            routes.append(root+'/messages?view_id='+feed['data']['view_id'])
            await asyncio.gather(*(call(ticker,route) for route in routes))
        context=await call('ALL','/read-context?page=OVERVIEW&period=CURRENT_TRADING_DAY')
        if context:
            view=context['data']['view_id']
            await asyncio.gather(*(call('ALL','/overview/'+name+'?view_id='+view)
                                  for name in ['metrics','tickers']))
        print(json.dumps(dict(total=len(results),failures=sum(r['status']!=200 for r in results))),flush=True)
    finally:await runner.close()


if __name__=='__main__':asyncio.run(main())
