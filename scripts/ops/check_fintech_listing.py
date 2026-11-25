"""Read-only exact production ListingAdapter probe; never inserts messages."""
import asyncio
import json
from types import SimpleNamespace

from doxagent.message_bus_v2.fintech_sources import initial_sources, listing_recipes
from doxagent.message_bus_v2.poll_trace import REQUESTS
from doxagent.message_bus_v2.public_listing import ListingAdapter
from doxagent.message_bus_v2.scheduler import SchedulerGroupLimiter
from doxagent.message_bus_v2.schema import TickerSourceBinding, utc_now
from doxagent.message_bus_v2.technology_sources import TechnologyAdapter, TechnologyHttp


async def main():
    http=TechnologyHttp();delegate=TechnologyAdapter(http);adapter=ListingAdapter(delegate,http)
    slots=asyncio.Semaphore(2)
    async def one(source,recipe):
        async with slots:
            binding=TickerSourceBinding(binding_id='COIN:'+source.source_id,ticker='COIN',
                source_id=source.source_id,polling=source.default_polling_config)
            limiter=SchedulerGroupLimiter(source.scheduler_constraints)
            context=SimpleNamespace(source=source,binding=binding,bindings=[binding],ticker='COIN',
                checkpoint={},requested_at=utc_now(),force_refresh=True,window_cutoff=None,
                window_start=None,request_permit=limiter.permit,acquisition_unit='HEAD')
            events=[];token=REQUESTS.set(events)
            try:
                result=await adapter._primary(context,recipe,shared=source.acquisition_mode.shared_distribution)
                print(json.dumps(dict(source=source.source_id,entry=recipe.feed,at=context.requested_at.isoformat(),
                    items=len(result.messages),failures=[{'code':f.error_code,'reason':f.error_message} for f in result.failures],
                    method=result.acquisition_metadata.get('method'),cache=result.acquisition_metadata.get('cache'),
                    validated_requests=len(result.acquisition_metadata.get('validated_request_ids',[])),
                    fallback_validated_requests=len(result.acquisition_metadata.get('fallback_validated_request_ids',[])),
                    time_bases=sorted({m.publication_time_basis for m in result.messages}))),flush=True)
            except Exception as e:
                print(json.dumps(dict(source=source.source_id,entry=recipe.feed,error=type(e).__name__+':'+str(e))),flush=True)
            finally:REQUESTS.reset(token)
    try:
        sources={s.source_id:s for s in initial_sources()}
        await asyncio.gather(*(one(sources[s],r) for s,recipes in listing_recipes().items() for r in recipes))
    finally:await http.close()


if __name__=='__main__':asyncio.run(main())
