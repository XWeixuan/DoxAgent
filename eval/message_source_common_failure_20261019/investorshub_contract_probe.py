"""Real listing acceptance with explicit ticker; no message ingestion or publishing."""
import asyncio, json, time
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessRequest, AccessMode, SitePurpose
from doxagent.message_bus_v2.market_sources import parse_investorshub
from doxagent.message_bus_v2.schema import utc_now


async def main():
    settings=DoxAgentSettings()
    client=SiteAccessClient(settings.site_access_url,token=read_token(settings.site_access_worker_token, settings.site_access_worker_token_file))
    try:
        for ticker in ['INTC','COHR']:
            result=await client.execute(AccessRequest(
                operation_id=f'contract-repair-20261019:{ticker}:{time.time_ns()}',
                origin_scope_id='source:investorshub_ticker_news',
                purpose=SitePurpose.CRAWLER, mode=AccessMode.BROWSER,
                url=f'https://investorshub.advfn.com/stock-market/NASDAQ/{ticker}/news',
                recipe_parameters={'ticker':ticker}, remaining_budget_ms=45000,
            ))
            rows=parse_investorshub(result.body,ticker=ticker,now=utc_now()) if result.disposition.value=='SUCCESS' else []
            print(json.dumps(dict(ticker=ticker,disposition=result.disposition.value,
                                 reason=result.reason_code,status=result.status_code,parsed=len(rows),
                                 identity=result.combination_id)),flush=True)
    finally:
        await client.close()

asyncio.run(main())
