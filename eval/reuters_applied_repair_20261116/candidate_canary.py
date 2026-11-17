import asyncio,json,argparse
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import quote
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.content_enrichment.quality import inspect_html,choose_candidate
s=DoxAgentSettings();args=argparse.ArgumentParser();args.add_argument('--query',default='Intel');args.add_argument('--body-url');args.add_argument('--title');a=args.parse_args()
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+Path(s.site_access_worker_token_file).read_text().strip()},timeout=65,trust_env=False) as h:
  req={'operation_id':'reuters-backup2-canary-'+datetime.now(timezone.utc).strftime('%H%M%S'),'origin_scope_id':'source:reuters_site_search','purpose':'BODY' if a.body_url else 'CRAWLER','url':a.body_url or 'https://www.reuters.com/site-search/?query='+quote(a.query)+'&offset=0','mode':'BROWSER','remaining_budget_ms':45000}
  if a.body_url:req['excluded_combinations']=['reuters-backup']
  if not a.body_url:req.update(recipe_ref='builtin:reuters_search@1',recipe_parameters={'query':a.query,'offset':0})
  r=await h.post('/v1/access/execute',json=req);r.raise_for_status();v=r.json();p=v.get('recipe_result') or {};out={k:v.get(k) for k in ['disposition','reason_code','status_code','identity_id','runtime_instance_id','queue_wait_ms','network_ms','served_from_shared_capture','attempts']};out.update(at=datetime.now(timezone.utc).isoformat(),query=a.query if not a.body_url else None,rows=len(p.get('rows',[])),sample_articles=[{k:r.get(k) for k in ['url','title']} for r in p.get('rows',[])[:3]])
  if a.body_url:
   i=inspect_html(v.get('body',''),a.body_url,a.title,strategy_ref='builtin:reuters@1');c,o,reason=choose_candidate(i,a.title);out['content']={'outcome':o,'reason':reason,'page_kind':i.page_kind,'candidate_chars':len(c.text) if c else 0}
  print(json.dumps(out,indent=2))
asyncio.run(main())
