import concurrent.futures,json,time,urllib.request,urllib.error
from pathlib import Path
URLS={
'aviation_rocketlab':'https://aviationweek.com/term/rocket-lab',
'aviation_feed':'https://aviationweek.com/awn/rss-feed-by-content-source',
'breaking_space':'https://breakingdefense.com/category/space/feed/',
'scoop_space':'https://defensescoop.com/news/space/feed/',
'air_space':'https://www.airandspaceforces.com/category/space/feed/',
'spacecom':'https://www.space.com/feeds/tag/launches-spacecraft',
'spacenews':'https://spacenews.com/feed/',
'ssc':'https://www.ssc.spaceforce.mil/Newsroom',
'nasa':'https://www.nasa.gov/2026-news-releases/',
'dod_contracts':'https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=10',
'dod_releases':'https://www.war.gov/news/rss_releases2.xml',
'iridium':'https://investor.iridium.com/press-releases',
'calcalist':'https://www.calcalist.co.il/category/3735?cx=partner-pub-0047680307646695:9309925491&cof=FORID:10&ie=UTF-8&q=Ondas',
'scoop_drones':'https://defensescoop.com/tag/drones/feed/',
'scoop_counter':'https://defensescoop.com/tag/counter-drone/feed/',
'unmanned_systems':'https://www.unmannedairspace.info/category/counter-uas-systems-and-policies/feed/',
'unmanned_tenders':'https://www.unmannedairspace.info/category/counter-uas-systems-tenders/feed/',
'shephard':'https://www.shephardmedia.com/news/uv-online/feed/',
'army_releases':'https://www.army.mil/news/newsreleases',
'army_feed':'https://www.army.mil/rss/static/0.xml',
}
def run(row):
 k,u=row;t=time.monotonic();r={'id':k,'url':u}
 try:
  op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  req=urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0','Accept':'*/*','Accept-Encoding':'identity'})
  try: response=op.open(req,timeout=8)
  except urllib.error.HTTPError as exc: response=exc
  with response:
   b=response.read(4*1024*1024+1);r.update(status=response.status,final_url=response.url,bytes=len(b),content_type=response.headers.get('Content-Type'),etag=response.headers.get('ETag'),last_modified=response.headers.get('Last-Modified'))
   Path(out,k+'.txt').write_bytes(b)
 except Exception as exc:r['error']=type(exc).__name__+':'+str(exc)
 r['ms']=round((time.monotonic()-t)*1000);return r
if __name__=='__main__':
 import sys
 out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:r=list(pool.map(run,URLS.items()))
 (out/'probe.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
 for a in r:print(json.dumps(a,ensure_ascii=False))
