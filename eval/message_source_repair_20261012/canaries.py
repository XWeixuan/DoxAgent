"""Owner-controlled bounded canaries. Does not publish articles or alter login state."""
import asyncio,json,sys,time
from datetime import UTC,datetime
import httpx
from lxml import html
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token

async def main():
    s=DoxAgentSettings(); out={'observed_at':datetime.now(UTC).isoformat()}
    admin=read_token(s.site_access_admin_token,s.site_access_admin_token_file)
    worker=read_token(s.site_access_worker_token,s.site_access_worker_token_file)
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',timeout=80,headers={'Authorization':'Bearer '+admin}) as c:
        if sys.argv[-1]=='managed':
            key='generic-c88e77b20ac3-jp-standard-6'
            r=await c.post('/v1/profiles/'+key+':probe',json={'url':'https://investorshub.advfn.com/stock-market/NYSE/bloom-energy-BE/news'})
            out['investorshub_managed']={'status':r.status_code,'result':r.json()}
        else:
            for key,url in [('reuters-1','https://www.reuters.com/site-search/?query=Micron'),('digitimes-nl-1','https://www.digitimes.com/topic/semiconductors/'),('dowjones-main','https://www.barrons.com/market-data/stocks/mu')]:
                try:
                    r=await c.post('/v1/profiles/'+key+':probe',json={'url':url})
                    out[key]={'status':r.status_code,'result':r.json()}
                except Exception as e: out[key]={'error':type(e).__name__}
            for path in ['latest-news/','news/']:
                payload={'operation_id':'diagnostic-20261012-yahoo-'+path.replace('/',''), 'purpose':'CRAWLER','url':'https://finance.yahoo.com/quote/MU/'+path,'mode':'BROWSER','remaining_budget_ms':35000}
                try:
                    r=await c.post('/v1/access/execute',json=payload,headers={'Authorization':'Bearer '+worker}); v=r.json()
                    body=v.pop('body','');v.pop('headers',None)
                    out['yahoo_'+path]={'result':v,'html_chars':len(body)}
                    if body:
                        tree=html.fromstring(body)
                        out['yahoo_'+path]['hydration_urls']=[x.split('?')[0] for x in tree.xpath('//script[@data-sveltekit-fetched]/@data-url')][:20]
                        out['yahoo_'+path]['article_links']=list(dict.fromkeys(tree.xpath('//a[contains(@href,"/news/")]/@href')))[:6]
                except Exception as e: out['yahoo_'+path]={'error':type(e).__name__}
    print(json.dumps(out,ensure_ascii=False),flush=True)
asyncio.run(main())
