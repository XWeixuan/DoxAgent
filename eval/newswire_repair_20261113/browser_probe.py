import asyncio,json,time
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.site_strategy.schema import AccessRequest
from doxagent.content_enrichment.quality import inspect_html,choose_candidate
async def main():
 s=DoxAgentSettings();c=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file));out=[]
 try:
  url='https://www.newsfilecorp.com/release/323833/Mayfair-Gold-Reports-Q3-2026-Operating-and-Financial-Results'
  resolved=await c.resolve(url);t=time.monotonic()
  result=await c.execute(AccessRequest(operation_id='newswire-body-diagnostic-'+str(time.time_ns()),purpose='BODY',mode='BROWSER',url=url,
      remaining_budget_ms=25000,recipe_parameters={'wait_selector':'h1:not(:has-text("JavaScript is disabled"))'}))
  text=result.body or '';Path('/tmp/newswire-tmx-browser.html').write_text(text)
  i=inspect_html(text,result.final_url or url,'Mayfair Gold Reports Q3 2026 Operating and Financial Results');candidate,outcome,reason=choose_candidate(i,'Mayfair Gold Reports Q3 2026 Operating and Financial Results')
  out.append({'site':resolved.site_id,'access_order':resolved.body.access_order,'seconds':round(time.monotonic()-t,3),'status':result.status_code,'disposition':result.disposition.value,'reason':result.reason_code,'identity':result.identity_id,'runtime':result.runtime_kind,'bytes':len(text),'outcome':outcome,'body_reason':reason,'chars':len(candidate.text) if candidate else 0,'attempts':[a.model_dump(mode='json') for a in result.attempts]})
 finally:await c.close()
 print(json.dumps(out))
asyncio.run(main())
