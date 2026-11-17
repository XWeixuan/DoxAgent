import asyncio,json,time,os,sqlite3
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.tokens import read_token
from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.transport import DEADLINE
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
async def main():
 s=DoxAgentSettings();c=sqlite3.connect('file:'+os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']+'?mode=ro',uri=True)
 url,title=c.execute('select url,title from live_articles where article_key=?',('9fb7642772d774efff55951b6ccbbaaf98cca1a8a2035ef4407709cbdee9319a',)).fetchone();c.close()
 client=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 extractor=SharedContentExtractor(site_access_client=client,close_site_access_client=True)
 token=DEADLINE.set(time.monotonic()+20);start=time.monotonic()
 try:
  r=await extractor.extract_version(MediaEnrichmentRecord('diagnostic','diagnostic','barrons_ticker_news','MU',title,None,url),'body_v2.2')
  out={'seconds':round(time.monotonic()-start,3),'outcome':r.diagnostics.get('outcome'),'reason':r.reason,'chars':len(r.content or ''),
       'attempts':[{'phase':a.phase,'reason':a.reason,'latency_ms':a.latency_ms} for a in r.attempts],
       'stage_timings':r.diagnostics.get('site_stage_timings')}
 except Exception as e:out={'seconds':round(time.monotonic()-start,3),'error':type(e).__name__}
 finally:DEADLINE.reset(token);await extractor.close()
 print(json.dumps(out))
asyncio.run(main())
