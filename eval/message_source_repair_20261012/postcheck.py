"""Read-only live owner/production checks. Never attaches another controller."""
import asyncio,json,sqlite3
from datetime import UTC,datetime
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
async def main():
 s=DoxAgentSettings();out={'at':datetime.now(UTC).isoformat()}
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',timeout=20,headers={'Authorization':'Bearer '+read_token(s.site_access_admin_token,s.site_access_admin_token_file)}) as c:
  out['ready']=(await c.get('/readyz')).json();out['diagnostics']=(await c.get('/v1/diagnostics/access')).json()
  out['identities']={}
  for identity in ('dowjones-main','digitimes-nl-1','digitimes-de-1','public-markets-nl','yahoo-main','seeking-alpha-main'):
   v=(await c.get('/v1/identities/'+identity)).json();out['identities'][identity]=v.get('live',v)
  out['candidates']={}
  for site,url in [('barrons','https://www.barrons.com/market-data/stocks/mu'),('reuters','https://www.reuters.com/site-search/?query=Micron'),('investing','https://www.investing.com/equities/intel-corp-news'),('investorshub','https://investorshub.advfn.com/stock-market/NYSE/bloom-energy-BE/news'),('digitimes','https://www.digitimes.com/topic/semiconductors/')]:
   out['candidates'][site]=(await c.get('/v1/diagnostics/candidates',params={'url':url})).json()
 db=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True);db.row_factory=sqlite3.Row
 out['bodies_since_release']=[dict(r) for r in db.execute("SELECT final_site_id,outcome,reason,count(*) n FROM body_outcomes WHERE julianday(completed_at)>=julianday('2026-10-12T12:30:24Z') GROUP BY 1,2,3")]
 out['access_since_release']=[dict(r) for r in db.execute("SELECT site_id,category,count(*) n,max(occurred_at) last FROM access_events WHERE occurred_at>='2026-10-12T12:30:24Z' GROUP BY 1,2")]
 print(json.dumps(out,ensure_ascii=False))
asyncio.run(main())
