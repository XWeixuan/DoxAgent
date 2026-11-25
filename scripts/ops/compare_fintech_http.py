"""Read-only content snapshots for browser comparison; no Bus admission."""
import asyncio
import json
from dataclasses import replace
from datetime import UTC,datetime
from pathlib import Path

from doxagent.message_bus_v2.fintech_sources import SOURCES,listing_recipes
from doxagent.message_bus_v2.fintech_parsers import parse_listing
from doxagent.message_bus_v2.technology_sources import TechnologyHttp,parse_listing as parse_feed
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as parse_ir


async def main():
    http=TechnologyHttp();ir=PublicHttp();slots=asyncio.Semaphore(2)
    root=Path('/tmp/fintech-freshness-raw');root.mkdir(exist_ok=True)
    async def one(sid,recipe,index):
        async with slots:
            row={'source':sid,'index':index,'url':recipe.url,'at':datetime.now(UTC).isoformat()}
            try:
                spec=SOURCES[sid];c=await http.fetch(recipe.url,spec,method=recipe.method)
                (root/(sid+'-'+str(index)+'.private.txt')).write_text(c.text)
                row.update(status=c.status,headers={k:c.headers.get(k) for k in ['age','date','cache-control','x-cache','cf-cache-status']})
                if c.status==200:
                    try:messages=parse_listing(recipe,c.text,datetime.now(UTC))
                    except Exception as e:row['parse_error']=str(e);messages=[]
                    row['messages']=[dict(title=m.title,url=m.url,published_at=m.published_at.isoformat(),basis=m.publication_time_basis) for m in messages]
                if sid in {'unchained_crypto','sbcamericas_prediction_markets'}:
                    feed=await http.fetch(recipe.feed,spec)
                    row['rss_status']=feed.status
                    if feed.status==200:
                        ms=parse_feed(replace(spec,parser='rss'),feed.text,feed.url,datetime.now(UTC))
                        row['rss_messages']=[dict(title=m.title,url=m.url,published_at=m.published_at.isoformat(),basis=m.publication_time_basis) for m in ms]
                        (root/(sid+'-'+str(index)+'.rss.private.txt')).write_text(feed.text)
            except Exception as e:row['error']=type(e).__name__+':'+str(e)[:180]
            print(json.dumps(row),flush=True)
    async def deribit():
        s=next(s for s in initial_endpoints() if s.endpoint_id=='coin.deribit');row={'source':'deribit','url':s.entry_url,'at':datetime.now(UTC).isoformat()}
        try:
            s=s.model_copy(update={'parser_parameters':{k:v for k,v in s.parser_parameters.items() if k!='max_listing_cache_age_seconds'}})
            c=await ir.fetch(s);row.update(status=c.status,headers={k:c.headers.get(k) for k in ['age','date','cache-control','x-cache']})
            (root/'deribit.private.txt').write_text(c.body)
            if c.status==200:
                ms,_=parse_ir(s,c.body);row['messages']=[dict(title=m.title,url=m.url,published_at=m.published_at.isoformat(),basis=m.publication_time_basis) for m in ms]
        except Exception as e:row['error']=type(e).__name__+':'+str(e)[:180]
        print(json.dumps(row),flush=True)
    try:
        targets={'unchained_crypto','sbcamericas_prediction_markets','thedefiant_fintech_regulation','cftc_trading_rules','cftc_press_releases'}
        await asyncio.gather(*(one(s,r,i) for s,rs in listing_recipes().items() if s in targets for i,r in enumerate(rs)),deribit())
    finally:await http.close();await ir.close()


if __name__=='__main__':asyncio.run(main())
