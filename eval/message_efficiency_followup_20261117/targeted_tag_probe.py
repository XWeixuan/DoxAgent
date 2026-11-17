"""Bounded public-listing inspection. No crawler state, cookies, or model calls."""
import asyncio
import json
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx
from lxml import html

from doxagent.message_bus_v2.http_routes import HTTP_EGRESSES
from doxagent.message_bus_v2.publisher_http import PublisherClients

ENDPOINTS = [
    ('gnw_listing','GET','https://www.globenewswire.com/en/Search/load/more?page=1&pageSize=50',{},None),
    ('gnw_rss','GET','https://www.globenewswire.com/RssFeed',{},HTTP_EGRESSES['us-standard-5']),
    ('gnw_feed_directory','GET','https://www.globenewswire.com/Rss/List',{},HTTP_EGRESSES['us-standard-5']),
    ('prn_listing','GET','https://www.prnewswire.com/news-releases/news-releases-list/?page=1&pagesize=100',{},None),
    ('prn_rss','GET','https://www.prnewswire.com/rss/news-releases-list.rss',{},None),
    ('bw_listing','POST','https://www.businesswire.com/newsroom?page=1',{'RSC':'1'},None),
    ('access_listing','GET','https://www.accessnewswire.com/newsroom?page=1',{},HTTP_EGRESSES['us-standard-5']),
    ('tmx_rss','GET','https://feeds.newsfilecorp.com/global/Last25Stories',{},None),
]

async def one(name,method,url,headers,proxy):
    out={'name':name,'url':url,'method':method,'at':datetime.now(UTC).isoformat()}
    try:
        client=PublisherClients()
        try:
            r=await client.request((name,'audit'),method,url,proxy=proxy,timeout=15,headers=headers,allow_redirects=True)
        finally:
            await client.close()
        out.update(status=r.status_code,bytes=len(r.content),content_type=r.headers.get('content-type'))
        if r.status_code!=200:return out
        text=r.text
        if name.endswith('_rss'):
            root=ET.fromstring(text);items=root.findall('.//item');out['items']=len(items)
            out['field_counts']={}
            for item in items:
                for e in item:
                    out['field_counts'][e.tag]=out['field_counts'].get(e.tag,0)+1
            out['samples']=[]
            for item in items[:8]:
                sample={}
                for e in item:
                    if any(k in e.tag.lower() for k in ['title','link','category','subject','industr','tag','ticker','company','author','keyword']):
                        sample.setdefault(e.tag,[]).append({'text':' '.join(e.itertext())[:400],'attributes':dict(e.attrib)})
                out['samples'].append(sample)
        elif name=='bw_listing':
            for m in re.finditer(r'"news":\s*(?=\{)',text):
                try:d,_=json.JSONDecoder().raw_decode(text[m.end():])
                except ValueError:continue
                if isinstance(d,dict) and isinstance(d.get('items'),list):
                    out['news_keys']=list(d);out['items']=len(d['items'])
                    out['samples']=[{k:v for k,v in it.items() if k not in ['teaser','image','imageUrl']} for it in d['items']]
                    out['item_field_counts']={k:sum(k in it for it in d['items']) for k in set().union(*(set(i) for i in d['items']))}
                    break
            # HTML fallback can expose per-card tags even when the JSON parser misses them.
            out['taxonomy_contexts']=[text[max(0,m.start()-80):m.end()+220] for m in list(re.finditer(r'(?i)class action|market research|industryTags|subjectTags|industries|subjects',text))[:12]]
        else:
            root=html.fromstring(text)
            if name=='gnw_feed_directory':
                out['classification_links']=[{'name':' '.join(a.itertext()).strip(),'href':a.get('href')} for a in root.xpath('//a[@href]') if any(k in a.get('href','') for k in ['subjectcode','industry/'])]
            else:
                xp={'gnw_listing':'//div[contains(concat(" ",normalize-space(@class)," ")," newsLink ")]','prn_listing':'//a[contains(concat(" ",normalize-space(@class)," ")," newsreleaseconsolidatelink ")]','access_listing':'//*[contains(concat(" ",normalize-space(@class)," ")," nr-card-body ")]'}[name]
                cards=root.xpath(xp);out['cards']=len(cards)
                out['sample_cards']=[html.tostring(n,encoding='unicode')[:8000] for n in cards[:3]]
                out['taglike_attributes']=[{'key':k,'value':v} for card in cards for n in card.iter() for k,v in n.attrib.items() if any(w in k.lower() for w in ['categor','tag','subject','industr'])][:30]
                out['filter_inputs']=[{'tag':n.tag,'id':n.get('id'),'name':n.get('name'),'value':n.get('value'),'text':' '.join(n.itertext()).strip()[:180]} for n in root.xpath('//select|//input')][:35]
    except Exception as e:out['error']=type(e).__name__+':'+str(e)[:160]
    return out

async def main():
    # Two requests at a time, with each publisher read only a bounded number of times.
    sem=asyncio.Semaphore(2)
    async def limited(args):
        async with sem:return await one(*args)
    rows=await asyncio.gather(*(limited(args) for args in ENDPOINTS))
    print(json.dumps(rows,ensure_ascii=False))

ENDPOINTS=[
('gnw_rss','GET','https://www.globenewswire.com/RssFeed/subjectcode/84-Class%20Action/feedTitle/GlobeNewswire%20-%20Class%20Action',{},HTTP_EGRESSES['us-standard-5']),
('gnw_rss','GET','https://www.globenewswire.com/RssFeed/subjectcode/81-Market%20Research%20Reports/feedTitle/GlobeNewswire%20-%20Market%20Research%20Reports',{},HTTP_EGRESSES['us-standard-5']),
('bw_listing','POST','https://www.businesswire.com/newsroom?page=3',{'RSC':'1'},None),
('bw_listing','POST','https://www.businesswire.com/newsroom?page=4',{'RSC':'1'},None),
]
asyncio.run(main())
