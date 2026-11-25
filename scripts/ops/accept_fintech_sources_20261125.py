"""Read-only real HTTP/list/BODY canary. No Bus intake, model calls or Chrome."""
import asyncio,json,time,argparse
from dataclasses import replace
from types import SimpleNamespace
from datetime import UTC,datetime

from doxagent.message_bus_v2.fintech_sources import SOURCES,listing_recipes,initial_sources,OCC_TYPES
from doxagent.message_bus_v2.fintech_parsers import parse_listing
from doxagent.message_bus_v2.technology_sources import TechnologyHttp,parse_listing as parse_feed
from doxagent.message_bus_v2.technology_body import TechnologyBodyReader
from doxagent.message_bus_v2.public_listing import cache_evidence
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as parse_ir
from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
from doxagent.message_bus_v2.corporate_ir.body import IrBodyReader
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--all-entries',action='store_true');parser.add_argument('--ir-only',action='store_true');parser.add_argument('--source');args=parser.parse_args()
    http=TechnologyHttp(purpose='BODY');ir_http=PublicHttp();slots=asyncio.Semaphore(2)
    definitions={s.source_id:s for s in initial_sources()}
    async def one(source_id,recipe):
        async with slots:
            spec=SOURCES[source_id];start=time.monotonic();report={'source':source_id,'entry':recipe.feed,'listing_url':recipe.url,'at':datetime.now(UTC).isoformat()}
            try:
                if source_id in {'sbcamericas_prediction_markets','unchained_crypto'} and recipe.coverage_review:
                    capture=await http.fetch(recipe.feed,spec)
                    messages=parse_feed(replace(spec,parser='rss'),capture.text,capture.url,datetime.now(UTC))
                    report['lane']='rss_fallback'
                else:
                    url=recipe.url+('&' if '?' in recipe.url else '?')+'_dox_poll='+str(int(time.time())//20) if recipe.fresh_query else recipe.url
                    data=dict(sId=101516,SearchTerm='',Topics=[],NewsTypes=list(OCC_TYPES),EarliestPublishDateTime=None,LatestPublishDateTime=None,SortByDate=None,StartFrom=0,Row=40) if source_id=='occ_newsroom' else None
                    capture=await http.fetch(url,spec,method=recipe.method,json_body=data,headers={'Cache-Control':'no-cache'})
                    messages=parse_listing(recipe,capture.text,datetime.now(UTC))
                    report['lane']='http'
                report.update(list_status=capture.status,list_count=len(messages),list_bytes=len(capture.text.encode()),headers={k:capture.headers.get(k) for k in ['age','date','cache-control','etag','last-modified']})
                try:report['cache']=cache_evidence(capture,recipe)
                except Exception as e:report['cache_error']=str(e)
                m=messages[0];record=MediaEnrichmentRecord('readonly','readonly',source_id,'COIN',m.title,'',m.url)
                job=SimpleNamespace(source=definitions[source_id],message=m,binding=SimpleNamespace(ticker='COIN'))
                async with asyncio.timeout(35):result=await TechnologyBodyReader(http).extract(job,record)
                report.update(title=m.title,url=m.url,published_at=m.published_at.isoformat(),time_basis=m.publication_time_basis,
                    body_succeeded=result.succeeded,body_outcome=result.diagnostics.get('outcome'),body_reason=result.reason,
                    body_chars=len(result.content or ''),body_method=result.extraction_method,
                    attachments=result.diagnostics.get('attachments'))
                if not result.succeeded and source_id not in {'cftc_trading_rules'} and spec.transport!='urllib':
                    p=await http.fetch(m.url,spec,method='POST')
                    from doxagent.message_bus_v2.corporate_ir.body import inspect_article
                    candidate,outcome,reason=await asyncio.to_thread(inspect_article,p.text,p.url,m.title) if p.status==200 else (None,None,None)
                    report['post_comparison']={'status':p.status,'outcome':outcome,'chars':len(candidate.text) if candidate else 0,'reason':reason}
            except Exception as e:report['error']=type(e).__name__+':'+str(e)[:250]
            report['seconds']=round(time.monotonic()-start,3);print(json.dumps(report),flush=True)
    async def ir_one(spec):
        async with slots:
            report={'source':'corporate_ir_press_releases','endpoint':spec.endpoint_id,'at':datetime.now(UTC).isoformat()}
            try:
                # A raw diagnostic capture can still exercise BODY when the
                # production listing freshness guard correctly rejects its Age.
                unchecked=spec.model_copy(update={'parser_parameters':{k:v for k,v in spec.parser_parameters.items() if k!='max_listing_cache_age_seconds'}})
                c=await ir_http.fetch(unchecked)
                report.update(list_status=c.status,headers={k:c.headers.get(k) for k in ['age','date','cache-control']})
                try:report['cache']=cache_evidence(c,SimpleNamespace(max_age_seconds=30))
                except Exception as e:report['cache_error']=str(e)
                if c.status!=200:raise ValueError('listing_http_'+str(c.status))
                messages,_=parse_ir(spec,c.body)
                report['list_count']=len(messages)
                m=messages[0];rec=MediaEnrichmentRecord('readonly','readonly','corporate_ir_press_releases','COIN',m.title,'',m.url)
                result=await IrBodyReader(ir_http).extract(SimpleNamespace(message=m,binding=SimpleNamespace(ticker='COIN')),rec)
                report.update(title=m.title,url=m.url,published_at=m.published_at.isoformat(),time_basis=m.publication_time_basis,
                    body_outcome=result.diagnostics.get('outcome'),body_reason=result.reason,body_chars=len(result.content or ''))
            except Exception as e:report['error']=type(e).__name__+':'+str(e)[:250]
            print(json.dumps(report),flush=True)
    try:
        await asyncio.gather(*(one(s,r) for s,rs in listing_recipes().items() for r in (rs if args.all_entries else rs[:1]) if not args.ir_only and (not args.source or s==args.source)),
                            *(ir_one(s) for s in initial_endpoints() if s.owner_ticker=='COIN' and not args.source))
    finally:
        await http.close();await ir_http.close()


asyncio.run(main())
