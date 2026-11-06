"""Read-only cache/frontend probes; no Bus ingestion. One or two sites per invocation."""
import asyncio
import base64
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from lxml import html

from doxagent.message_bus_v2.technology_sources import Capture, TechnologyHttp, TechnologySource

ROOT = Path('/data/diagnostics/rss-http-race-20261107')
HEADERS = {'age','cache-control','date','last-modified','etag','expires','x-cache',
           'x-cache-hits','x-served-by','cf-cache-status','x-drupal-cache',
           'x-drupal-dynamic-cache','via','server-timing','x-varnish','cdn-cache-control',
           'surrogate-control','x-proxy-cache','x-cache-status','vary','content-type'}


async def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    jobs = json.loads(base64.b64decode(sys.argv[1]))
    http = TechnologyHttp()
    try:
        for job in jobs:
            url = job['url']
            spec = TechnologySource(job['name'],job['name'],urlsplit(url).hostname.removeprefix('www.'),
                                    (url,),(),route=job.get('route','direct'),transport=job.get('transport','curl'))
            start = time.monotonic()
            out = {'name':job['name'],'requested_at':datetime.now(UTC).isoformat(),
                   'requested_url':url,'method':job.get('method','GET'),'route':spec.route,
                   'request_headers':job.get('headers',{'Cache-Control':'no-cache'})}
            try:
                if 'form' in job:
                    from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
                    response=await http.clients.request((spec.domain,spec.route),'POST',url,
                        proxy=HTTP_EGRESSES[spec.route],headers=out['request_headers'],data=job['form'],allow_redirects=False)
                    r=Capture(response.status_code,url,response.text,{k.lower():v for k,v in response.headers.items()})
                else:
                    r = await http.fetch(url,spec,headers=out['request_headers'],method=out['method'],
                                         **({'json_body':job['json_body']} if 'json_body' in job else {}))
                (ROOT/(job['name']+'.body')).write_text(r.text,encoding='utf8')
                out.update(status=r.status,url=r.url,headers={k:v for k,v in r.headers.items() if k.lower() in HEADERS},
                           bytes=len(r.text.encode()),sha256=hashlib.sha256(r.text.encode()).hexdigest())
                try:
                    d=html.fromstring(r.text)
                    out['title']=[t[:180] for t in d.xpath('//title/text()')][:2]
                    out['scripts']=[{'src':n.get('src'),'id':n.get('id'),'type':n.get('type'),'size':len(n.text or '')}
                                    for n in d.xpath('//script') if n.get('src') or len(n.text or '')>1000]
                    out['latest_links']=[{'title':' '.join(a.text_content().split())[:180],'href':a.get('href')}
                                         for a in d.xpath('//h1//a[@href]|//h2//a[@href]|//h3//a[@href]')][:12]
                except Exception:
                    pass
            except Exception as exc:
                out['error']=type(exc).__name__+':'+str(exc)[:220]
            out['seconds']=round(time.monotonic()-start,3)
            (ROOT/(job['name']+'.json')).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps({k:v for k,v in out.items() if k not in {'scripts','latest_links'}},ensure_ascii=True),flush=True)
    finally:
        await http.close()


asyncio.run(main())
