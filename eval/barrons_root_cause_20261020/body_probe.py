"""Two real business body captures; no message publication or credential output."""
import json
import subprocess

code=r'''import asyncio,json,time
from pathlib import Path
import httpx
from doxagent.content_enrichment.quality import inspect_html,choose_candidate
async def main():
 token=Path('/run/secrets/site_access_worker_token').read_text().strip()
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+token,'X-Site-Access-Protocol':'2'},trust_env=False) as client:
  for url in ['https://www.barrons.com/articles/micron-stock-cxmt-china-memory-chip-175b32ef','https://www.barrons.com/articles/intel-stock-upgrade-chip-selloff-328bd94d']:
   start=time.monotonic()
   response=await client.post('/v1/access/execute',json={'operation_id':'barrons-body-acceptance-'+str(time.time_ns()),'url':url,'purpose':'BODY','mode':'BROWSER','origin_scope_id':'source:barrons_ticker_news','remaining_budget_ms':45000},timeout=60)
   response.raise_for_status();r=response.json()
   response=await client.get('/v1/resolve',params={'url':url},timeout=10);response.raise_for_status();s=response.json();h=r.get('body','')
   i=inspect_html(h,r.get('final_url') or url,'',strategy_ref=s['body']['ref'],strategy_parameters=s['body']['parameters'])
   c,q,reason=choose_candidate(i,'')
   out={k:r.get(k) for k in ('disposition','status_code','reason_code','failure_category','identity_id','runtime_instance_id','effective_scope_id','network_ms','queue_wait_ms')}
   out.update(url=url,elapsed_seconds=time.monotonic()-start,quality=q,quality_reason=reason,article_chars=len(c.text) if c else 0,html_bytes=len(h.encode()),page_kind=i.page_kind,access_reason=i.access_reason,subscription_article=i.subscription_article)
   print(json.dumps(out),flush=True)
asyncio.run(main())
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-'],input=code,text=True,capture_output=True,timeout=150)
print(p.stdout)
assert p.returncode==0,p.stderr[-400:]
