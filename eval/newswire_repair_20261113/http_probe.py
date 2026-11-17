import asyncio,json,time,hashlib
from pathlib import Path
from curl_cffi.requests import AsyncSession
from lxml import html
from doxagent.content_enrichment.quality import inspect_html,choose_candidate

CASES=[
 ('bw-get','GET','https://www.businesswire.com/news/home/20261112795782/en/Helen-of-Troy-Issues-Statement/',{},'Helen of Troy Issues Statement'),
 ('bw-post','POST','https://www.businesswire.com/news/home/20261112795782/en/Helen-of-Troy-Issues-Statement/',{},'Helen of Troy Issues Statement'),
 ('bw-rsc','POST','https://www.businesswire.com/news/home/20261112795782/en/Helen-of-Troy-Issues-Statement/',{'RSC':'1'},'Helen of Troy Issues Statement'),
 ('prn-list-get','GET','https://www.prnewswire.com/news-releases/news-releases-list/?page=1&pagesize=100',{},''),
 ('prn-body','GET','https://www.prnewswire.com/news-releases/edun-brings-live-rosin-to-the-holiday-kitchen-with-limited-edition-organic-evoo-launch-302938320.html',{},'Edun Brings Live Rosin to the Holiday Kitchen With Limited-edition Organic EVOO Launch'),
 ('tmx-get','GET','https://www.newsfilecorp.com/release/323833/Mayfair-Gold-Reports-Q3-2026-Operating-and-Financial-Results',{},'Mayfair Gold Reports Q3 2026 Operating and Financial Results'),
 ('tmx-post','POST','https://www.newsfilecorp.com/release/323833/Mayfair-Gold-Reports-Q3-2026-Operating-and-Financial-Results',{},'Mayfair Gold Reports Q3 2026 Operating and Financial Results'),
]
async def main():
 out=[]
 async with AsyncSession(impersonate='chrome',timeout=8,trust_env=False) as s:
  for label,method,url,headers,title in CASES:
   t=time.monotonic()
   try:
    r=await s.request(method,url,headers=headers);text=r.text
    Path('/tmp/newswire-'+label+'.html').write_text(text)
    info=inspect_html(text,str(r.url),title);candidate,outcome,reason=choose_candidate(info,title)
    nodes=[]
    if text and '<html' in text.lower():
     root=html.fromstring(text)
     nodes=[{'tag':n.tag,'id':n.get('id'),'class':n.get('class'),'chars':len(n.text_content())} for n in root.xpath('//article|//*[@itemprop="articleBody"]|//*[contains(@class,"release-body")]|//*[@id="bw-release-story"]')][:12]
    out.append({'label':label,'url':str(r.url),'status':r.status_code,'seconds':round(time.monotonic()-t,3),'bytes':len(r.content),'sha256':hashlib.sha256(r.content).hexdigest(),'headers':{k:v for k,v in r.headers.items() if k.lower() in ['content-type','cache-control','age','date','cf-cache-status','x-amzn-waf-action','allow']},'outcome':outcome,'reason':reason,'access_reason':info.access_reason,'page_kind':info.page_kind,'headline':info.headline,'candidates':[{'method':c.method,'chars':len(c.text)} for c in info.candidates],'publisher_links':info.publisher_links,'expansion':info.expansion_required,'nodes':nodes})
   except Exception as e:out.append({'label':label,'error':type(e).__name__,'seconds':round(time.monotonic()-t,3)})
 print(json.dumps(out,ensure_ascii=False))
asyncio.run(main())
