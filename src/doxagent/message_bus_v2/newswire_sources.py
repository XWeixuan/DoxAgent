"""Global wire endpoints on the shared Collector and live delivery pipeline."""
import asyncio
import httpx
import hashlib
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

from lxml import html
from .schema import (AcquisitionMode, DistributionPolicy, PollingConfig, PollResult,
                     SchedulerConstraints, SourceDefinition, SourceKind, RawMessageInput)
from .industry_sources import parse_shared_feed
from .http_routes import HTTP_EGRESSES, transport_routes
from .publisher_http import PublisherHttpx, PublisherClients, RESPONSE_LIMIT
from .newswire_listing import RECIPES, enabled as listing_enabled, poll_listing

TICKERS=['INTC','MU','BE','COHR','NBIS','CEG','RKLB','SNOW','ONDS','IONQ']
CATALOG={
 'businesswire_massive':('Business Wire',[('all','https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJcFVJYWQ==')],['direct']),
 'prnewswire_massive':('PR Newswire',[('all','https://www.prnewswire.com/rss/news-releases-list.rss'),('business','https://www.prnewswire.com/rss/general-business-latest-news/general-business-latest-news-list.rss')],['direct']),
 'globenewswire_massive':('GlobeNewswire',[('all','https://www.globenewswire.com/RssFeed'),('public','https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies')],['tr-standard-1','us-standard-5']),
 'access_newswire_massive':('ACCESS Newswire',[('http','https://www.accessnewswire.com/newsroom')],['us-standard-5']),
 'tmx_newsfile_massive':('TMX Newsfile',[('all','https://feeds.newsfilecorp.com/global/Last25Stories')],['direct']),
 'bloomberg_massive':('Bloomberg',[(name,'https://www.bloomberg.com/'+path) for name,path in [('news','feeds/news.rss'),('economics','feeds/economics/news.rss'),('industries','feeds/industries/news.rss'),('technology','feeds/technology/news.rss'),('business','feeds/business/news.rss'),('markets','feeds/markets/news.rss'),('sitemap','sitemaps/news/latest.xml')]],['direct']),
}


def bloomberg_body_limitation(url):
    path=urlsplit(url).path
    if any(part in path for part in ('/news/audio/','/news/videos/')):
        return 'media_requires_transcript'
    if '/opinion/newsletters/' in path:
        return 'newsletter_requires_full_text'
    return None


def initial_sources():
    return [SourceDefinition(source_id=sid,display_name=name,kind=SourceKind.CRAWLER if sid in RECIPES else SourceKind.API,
        adapter_ref='builtin:newswire_massive@1',acquisition_mode=AcquisitionMode.MASSIVE_DISTRIBUTION,
        delivery_mode='test',entry_url=urls[0][1],content_language='en',scheduler_group='massive.'+sid,
        scheduler_constraints=SchedulerConstraints(max_concurrency=2 if sid=="bloomberg_massive" else 1,minimum_request_gap_seconds=1),
        distribution_policy=DistributionPolicy(test_tickers=TICKERS,jev_enabled=False,require_complete_body=True),
        default_parameters={'endpoints':[{'endpoint_id':eid,'url':url,'format':'html' if eid=='http' else 'sitemap' if eid=='sitemap' else 'rss'} for eid,url in urls],
                            'feed_http_routes':routes, **({'discovery_mode':'http_listing'} if sid in RECIPES else {})},default_polling_config=PollingConfig(target_interval_seconds=30))
        for sid,(name,urls,routes) in CATALOG.items()]


def endpoint_units(source):
    if listing_enabled(source):
        return [('TEST:' if source.delivery_mode=='test' else '')+'WIRE:listing']
    return [('TEST:' if source.delivery_mode=='test' else '')+'WIRE:'+e['endpoint_id']
            for e in source.default_parameters['endpoints']]


def parse_access(text,now):
    root=html.fromstring(text);rows=[]
    for card in root.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," nr-card-body ")]'):
        anchors=card.xpath('.//*[contains(@class,"nr-card-title")]/a');dates=card.xpath('.//*[contains(@class,"nr-card-meta")]')
        if not anchors or not dates:continue
        a=anchors[0];url=a.get('href','')
        if not url.startswith('https://www.accessnewswire.com/newsroom/en/'):continue
        published=datetime.strptime(dates[0].text_content().strip(),'%B %d, %Y %I:%M %p').replace(tzinfo=ZoneInfo('America/New_York'))
        summaries=card.xpath('.//*[contains(@class,"nr-card-summary")]')
        rows.append(RawMessageInput(title=a.text_content().strip(),summary=summaries[0].text_content().strip() if summaries else None,
            body=None,url=url,source='ACCESS Newswire',published_at=published,external_id=url.rsplit('-',1)[-1],
            raw_payload={'listing_url':'https://www.accessnewswire.com/newsroom','displayed_time':dates[0].text_content().strip()},
            metadata={'language':'en','publication_time_precision':'minute','identity_evidence':{'id_kind':'stable_article','url_kind':'article'}}))
    if not rows:raise ValueError('access_newsroom_structure_or_challenge')
    return rows


def parse_feed(text,source,endpoint,now):
    if endpoint['format']=='html':return parse_access(text,now)
    if endpoint['format']=='sitemap':
        root=ET.fromstring(text)
        if not root.tag.endswith('}urlset'):raise ValueError('invalid_news_sitemap')
        rows=[]
        for entry in root.findall('{*}url'):
            url=entry.findtext('{*}loc');title=entry.findtext('{*}news/{*}title')
            date=entry.findtext('{*}news/{*}publication_date')
            if not url or not title or not date:continue
            if urlsplit(url).hostname not in {'www.bloomberg.com','bloomberg.com'}:continue
            rows.append(RawMessageInput(title=title,url=url,source=source.display_name,
                published_at=datetime.fromisoformat(date.replace('Z','+00:00')),
                source_item_key=url,raw_payload={'sitemap_url':endpoint.get('url'),'publication_date':date},metadata={'language':'en','publication_time_precision':'second',
                'identity_evidence':{'id_kind':'stable_article','url_kind':'article'}}))
        if not rows:raise ValueError('news_sitemap_has_no_valid_articles')
        return rows
    # BW publishes HTTP links and its feed host is different from its article host.
    if source.source_id=='businesswire_massive':
        try:
            root=ET.fromstring(text)
        except ET.ParseError as exc:
            raise ValueError("wire_feed_invalid_xml") from exc
        for link in root.findall('.//item/link'):
            if (link.text or '').startswith('http://www.businesswire.com/'):
                link.text=link.text.replace('http://','https://',1)
        text=ET.tostring(root,encoding='unicode')
    base='https://www.businesswire.com/' if source.source_id=='businesswire_massive' else (
        'https://www.newsfilecorp.com/' if source.source_id=='tmx_newsfile_massive' else endpoint['url'])
    rows=parse_shared_feed(text,feed_url=base,publisher=source.display_name,language='en',now=now)
    for row in rows:
        parts=urlsplit(row.url).path.split('/')
        lang=parts[4] if source.source_id=='businesswire_massive' and len(parts)>4 else 'en'
        row.metadata['language']={'zh-HK':'zh-Hant','zh-CN':'zh-Hans'}.get(lang,lang)
        if source.source_id=='bloomberg_massive':
            # RSS descriptions are only listing teasers; sitemap has none.
            # Keep teaser evidence, but do not turn endpoint differences into body revisions.
            row.raw_payload['listing_summary']=row.summary
            row.summary=None
    return rows


class NewswireAdapter:
    def __init__(self):
        self.clients={};self.slots=None;self.curl=PublisherClients()

    async def close(self):
        for c in self.clients.values():await c.aclose()
        await self.curl.close()

    async def poll(self,context):
        raise RuntimeError('massive distribution requires shared polling')

    async def poll_shared(self,context):
        if listing_enabled(context.source):
            return await poll_listing(self,context)
        return await self.poll_feed(context)

    async def listing_request(self,context,url,recipe,headers):
        if self.slots is None:self.slots=asyncio.Semaphore(2)
        async with self.slots,context.request_permit():
            async def send(proxy):
                response=await self.curl.request((urlsplit(url).hostname,proxy),'POST' if recipe['method']=='POST' else 'GET',url,
                    proxy=proxy,headers=headers,allow_redirects=True,timeout=8)
                return httpx.Response(response.status_code,content=response.content,
                    headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-encoding','content-length'}},
                    request=httpx.Request(recipe['method'],url))
            return await transport_routes(send,[(r,HTTP_EGRESSES[r]) for r in recipe['routes']],
                (TimeoutError,OSError,httpx.TransportError))

    async def poll_feed(self,context):
        s=context.source;cp=dict(context.checkpoint or {})
        endpoint=next(e for e in s.default_parameters['endpoints'] if context.acquisition_unit.endswith('WIRE:'+e['endpoint_id']))
        headers={}
        if not context.force_refresh:
            if cp.get('etag'):headers['If-None-Match']=cp['etag']
            if cp.get('modified'):headers['If-Modified-Since']=cp['modified']
        if self.slots is None:self.slots=asyncio.Semaphore(2)
        started=time.monotonic()
        token=RESPONSE_LIMIT.set(16*1024*1024 if s.source_id=='businesswire_massive' else 4*1024*1024)
        try:
            async with self.slots,context.request_permit(),asyncio.timeout(12):
                async def send(proxy):
                    if proxy:
                        result=await self.curl.request((urlsplit(endpoint['url']).hostname,proxy),'GET',endpoint['url'],
                            proxy=proxy,headers=headers,allow_redirects=True,timeout=10)
                        # Normalize the existing curl transport into the shared parser contract.
                        return httpx.Response(result.status_code,content=result.content,headers={k:v for k,v in result.headers.items() if k.lower() not in {"content-encoding","content-length"}},
                            request=httpx.Request('GET',endpoint['url']))
                    key=proxy or 'direct'
                    if key not in self.clients:self.clients[key]=PublisherHttpx(proxy=proxy,timeout=10,trust_env=False)
                    return await self.clients[key].get(endpoint['url'],headers=headers)
                response,route=await transport_routes(send,[(r,HTTP_EGRESSES[r]) for r in s.default_parameters['feed_http_routes']],
                    (TimeoutError,OSError,httpx.TransportError))
            metadata={'wire_status':response.status_code,'route':route,'endpoint':endpoint['endpoint_id'],
                'request_seconds':round(time.monotonic()-started,3),'response_bytes':len(response.content)}
            if response.status_code==304:
                return PollResult(messages=[],next_checkpoint=cp,window_done=True,window_coverage='PARTIAL',
                    acquisition_metadata=metadata)
            response.raise_for_status()
            text=response.text;digest=hashlib.sha256(response.content).hexdigest()
            cp.update(etag=response.headers.get('etag'),modified=response.headers.get('last-modified'))
            if digest==cp.get('digest') and not context.force_refresh:
                return PollResult(messages=[],next_checkpoint=cp,window_done=True,window_coverage='PARTIAL',acquisition_metadata=metadata)
            rows=await asyncio.to_thread(parse_feed,text,s,endpoint,context.requested_at)
            old=cp.get('seen',{});seen={};delta=[];cutoff=context.requested_at-timedelta(minutes=30)
            for row in rows:
                if row.published_at<cutoff and s.source_id!='bloomberg_massive':continue
                hint=hashlib.sha256((str(row.title)+str(row.summary)).encode()).hexdigest()
                seen[row.source_item_key or row.url]=hint
                if context.force_refresh or old.get(row.source_item_key or row.url)!=hint:delta.append(row)
            overlap=len(set(old)&set(seen))
            metadata.update(feed_items=len(rows),delta_items=len(delta),window_overlap=overlap,
                window_overflow_risk=bool(old and seen and not overlap),
                oldest=min(r.published_at for r in rows).isoformat(),newest=max(r.published_at for r in rows).isoformat())
            cp.update(digest=digest,seen=seen)
            return PollResult(messages=delta,next_checkpoint=cp,window_done=True,window_coverage='PARTIAL',
                acquisition_metadata=metadata)
        finally:RESPONSE_LIMIT.reset(token)
