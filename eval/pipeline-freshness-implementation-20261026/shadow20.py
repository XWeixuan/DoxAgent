"""Eleven additional real tickers, acquisition only, alongside nine live 30s tickers."""
import asyncio
import json
import time
from datetime import UTC,datetime
from curl_cffi.requests import AsyncSession
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessRequest,AccessMode,SitePurpose
from doxagent.site_strategy.tokens import read_token
from doxagent.message_bus_v2.barrons_sources import render_news_feeds
from doxagent.message_bus_v2.industry_sources import parse_barrons_ticker_listing
from doxagent.message_bus_v2.market_sources import parse_investing,parse_investorshub
from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES

CASES=[('AAPL','apple-computer-inc'),('MSFT','microsoft-corp'),('NVDA','nvidia-corp'),
       ('AMD','adv-micro-device'),('AMZN','amazon-com-inc'),('GOOGL','google-inc'),
       ('META','facebook-inc'),('TSLA','tesla-motors'),('AVGO','avago-technologies'),
       ('ORCL','oracle-corp'),('CRM','salesforce-com')]

async def main():
    settings=DoxAgentSettings()
    client=SiteAccessClient(settings.site_access_url,token=read_token(settings.site_access_worker_token,settings.site_access_worker_token_file))
    session=AsyncSession(impersonate='chrome',trust_env=False)
    http_slots=asyncio.Semaphore(4)
    public_retry_at=0.0
    origin=time.monotonic()+1
    end=datetime.fromisoformat(__import__('sys').argv[1])
    async def one(round_number,index,ticker,slug,source,channel=None):
        nonlocal public_retry_at
        target=origin+round_number*30+index*30/len(CASES)+(30/(len(CASES)*2) if channel=="BarronsDowJonesNetwork" else 0)
        await asyncio.sleep(max(0,target-time.monotonic()))
        if datetime.now(UTC)>=end:return
        at=datetime.now(UTC);start=time.monotonic()
        out={'round':round_number,'ticker':ticker,'source_id':source,'channel':channel,'dispatch_at':at.isoformat(),'shadow':True}
        try:
            if source=='barrons_ticker_news':
                url=f'https://www.barrons.com/market-data/api/news?channel={channel}&chartingSymbol=stock%2F%2F%2F{ticker.lower()}&pageNumber=0&count=40'
                try:
                    if time.monotonic()<public_retry_at:raise RuntimeError('http_cooldown')
                    async with http_slots:
                        out['request_started_at']=datetime.now(UTC).isoformat()
                        r=await session.get(url,proxy=HTTP_EGRESSES['us-standard-5'],timeout=8,allow_redirects=False,headers={'Cache-Control':'no-cache'})
                    out.update(status_code=r.status_code,response_received_at=datetime.now(UTC).isoformat())
                    body=render_news_feeds([{'status':r.status_code,'text':r.text}],channels=[channel])
                    out['message_count']=len(parse_barrons_ticker_listing(body,ticker=ticker,now=at))
                    out['disposition']='SUCCESS'
                except Exception as exc:
                    if str(exc)!='http_cooldown':public_retry_at=time.monotonic()+60
                    out['http_fallback_reason']=type(exc).__name__
                    r=await client.execute(AccessRequest(operation_id=f'shadow20:barrons:{ticker}:{channel}:{round_number}:{at.timestamp()}',origin_scope_id='source:'+source,purpose=SitePurpose.CRAWLER,url=f'https://www.barrons.com/market-data/stocks/{ticker.lower()}',mode=AccessMode.BROWSER,recipe_ref='builtin:barrons_ticker@1',recipe_parameters={'ticker':ticker,'channel':channel,'_poll_due_at':at.isoformat(),'_poll_rate':40/30},remaining_budget_ms=max(1000,25000-int((time.monotonic()-start)*1000))))
                    out.update(disposition=r.disposition.value,reason=r.reason_code,network_ms=r.network_ms,queue_wait_ms=r.queue_wait_ms,stage_timings=r.stage_timings,shared=r.served_from_shared_capture)
                    if r.disposition.value=='SUCCESS':out['message_count']=len(parse_barrons_ticker_listing(r.body,ticker=ticker,now=at))
            else:
                hub=source=='investorshub_ticker_news'
                exchange='NYSE' if ticker in {'ORCL','CRM'} else 'NASDAQ'
                url=f'https://investorshub.advfn.com/stock-market/{exchange}/{ticker}/news' if hub else f'https://www.investing.com/equities/{slug}-news'
                recipe='builtin:investorshub_ticker@1' if hub else 'builtin:investing_ticker@1'
                r=await client.execute(AccessRequest(operation_id=f'shadow20:{source}:{ticker}:{round_number}:{at.timestamp()}',origin_scope_id='source:'+source,purpose=SitePurpose.CRAWLER,url=url,mode=AccessMode.BROWSER,recipe_ref=recipe,recipe_parameters={'ticker':ticker,'_poll_due_at':at.isoformat(),'_poll_rate':20/30},remaining_budget_ms=25000))
                out.update(disposition=r.disposition.value,reason=r.reason_code,network_ms=r.network_ms,queue_wait_ms=r.queue_wait_ms,stage_timings=r.stage_timings,shared=r.served_from_shared_capture)
                if r.disposition.value=='SUCCESS':out['message_count']=len((parse_investorshub if hub else parse_investing)(r.body,ticker=ticker,now=at))
        except Exception as exc:out.update(error_type=type(exc).__name__)
        out.update(completed_at=datetime.now(UTC).isoformat(),elapsed_ms=round((time.monotonic()-start)*1000))
        print(json.dumps(out),flush=True)
    try:
        await asyncio.gather(*(one(r,i,t,s,source,c) for r in range(6) for i,(t,s) in enumerate(CASES)
            for source,c in [('barrons_ticker_news','Barrons'),('barrons_ticker_news','BarronsDowJonesNetwork'),('investorshub_ticker_news',None),('investing_ticker_news',None)]))
    finally:
        await client.close();await session.close()

asyncio.run(main())
