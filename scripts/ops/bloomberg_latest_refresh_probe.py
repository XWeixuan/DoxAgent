"""Bounded paired Latest page reloads on the existing persistent lab Chrome.

No new identity, credentials, independent news stream, cache-busting URLs or Bus
writes. Restores normal local-cache behavior and leaves the existing tab open.
"""
import argparse
import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

ENTRY='https://www.bloomberg.com/latest'
CACHE_HEADERS={'age','cache-control','etag','last-modified','date','x-cache','via','vary'}
DOM='''()=>({title:document.title,challenge:!!document.querySelector('#px-captcha,.px-captcha'),serviceWorkerControlled:!!navigator.serviceWorker?.controller,rows:[...document.querySelectorAll('[class*="Latest_latestBody"] a[class*="Latest_storyLink"]')].filter(a=>a.textContent.trim()).map(a=>({url:a.href.split('?')[0],title:a.textContent.trim()}))})'''


def utc():return datetime.now(UTC).isoformat()


def canonical(url):
    p=urlsplit(urljoin(ENTRY,url))
    if p.scheme!='https' or p.hostname not in {'www.bloomberg.com','bloomberg.com'} or p.username or p.password:
        return None
    if not re.match(r'^/(news|opinion|features|graphics|newsletters)/',p.path):return None
    return urlunsplit(('https','www.bloomberg.com',p.path,'',''))


def metadata_rows(data):
    if not isinstance(data,list):raise ValueError('invalid_news_array')
    rows=[]
    for value in data:
        r=value.get('value') if isinstance(value,dict) and value.get('status')=='fulfilled' else value
        if not isinstance(r,dict):continue
        if not re.fullmatch(r'[A-Z0-9]{14}',str(r.get('id',''))):continue
        published=datetime.fromisoformat(r['publishedAt'].replace('Z','+00:00'))
        if published.tzinfo is None:raise ValueError('publication_timezone_missing')
        link=canonical(r.get('url',''))
        if link and r.get('headline'):
            rows.append({k:r.get(k) for k in ['id','headline','publishedAt','updatedAt']}|{'url':link})
    return rows


def endpoint(url):
    p=urlsplit(url)
    if p.hostname not in {'www.bloomberg.com','bloomberg.com'}:return None
    if p.path=='/latest':return 'document'
    if p.path=='/lineup-next/api/stories':
        q=parse_qs(p.query)
        return 'list_page_'+q.get('pageNumber',['unknown'])[0] if 'id' not in q else 'expanded_story'
    if p.path.startswith('/lineup-next/api/stories/'):return 'expanded_story'
    return None


async def run(args):
    from playwright.async_api import async_playwright
    from doxagent.settings import DoxAgentSettings
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.governance_repository import GovernanceRepository
    from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
    s=DoxAgentSettings();repo=SiteStrategyRepository(s.site_access_sqlite_path)
    sessions=[x for x in GovernanceRepository(repo).list_sessions()
              if x.identity_id=='bloomberg-latest-lab-1' and x.state in {'OPEN','RELEASED'}]
    if len(sessions)!=1:raise ValueError('require_one_existing_lab_session')
    status=await ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,
                 owner_id='bloomberg-refresh-study').status('bloomberg-latest-lab-1')
    if not status.get('running'):raise ValueError('existing_chrome_not_running')
    with Path(args.output).open('a',encoding='utf-8',buffering=1) as out:
        def emit(kind,**row):out.write(json.dumps({'kind':kind,'at':utc(),**row},ensure_ascii=True)+'\n')
        async with async_playwright() as pw:
            browser=await pw.chromium.connect_over_cdp(status['cdp_websocket_url'])
            pages=[p for c in browser.contexts for p in c.pages if p.url==ENTRY]
            if len(pages)!=1:raise ValueError('require_one_existing_latest_page')
            page=pages[0];cdp=await page.context.new_cdp_session(page)
            current={'cycle':'initial','mode':'initial','list':[],'responses':[]}
            requests={};request_cycles={};tasks=set();ready=asyncio.Event();dom_ready=asyncio.Event()
            def schedule(coro):
                t=asyncio.create_task(coro);tasks.add(t);t.add_done_callback(tasks.discard)
            def sent(e):
                if endpoint(e['request']['url']) is not None:
                    request_cycles[e['requestId']]={'cycle':current['cycle'],'mode':current['mode']}
            def request_headers(e):
                phase=request_cycles.get(e['requestId'])
                if phase:
                    emit('request_cache_headers',**phase,request_id=e['requestId'],headers={k.lower():v for k,v in e.get('headers',{}).items() if k.lower() in {'cache-control','pragma'}})
            def response(e):
                r=e['response'];which=endpoint(r['url'])
                if which is None:return
                phase=request_cycles.get(e['requestId'],{'cycle':'unattributed','mode':'unattributed'})
                row={**phase,'endpoint':which,
                     'request_id':e['requestId'],'status':r['status'],'response_headers_at':utc(),
                     'local_cache':r.get('fromDiskCache',False),'service_worker':r.get('fromServiceWorker',False),
                     'protocol':r.get('protocol'),'headers':{k.lower():v for k,v in r.get('headers',{}).items() if k.lower() in CACHE_HEADERS}}
                requests[e['requestId']]=row
                if row['cycle']==current['cycle']:current['responses'].append(row)
                emit('response',**row)
                if which=='list_page_1' and row['cycle']==current['cycle'] and r['status']!=200:ready.set()
            async def finished(e):
                row=requests.pop(e['requestId'],None)
                if not row or row['endpoint']=='document' or row['status']!=200:return
                try:
                    result=await cdp.send('Network.getResponseBody',{'requestId':e['requestId']})
                    if result.get('base64Encoded'):raise ValueError('unexpected_encoded_json')
                    items=metadata_rows(json.loads(result['body']))
                    emit('metadata',**{k:row[k] for k in ['cycle','mode','endpoint','request_id']},items=items)
                    if row['cycle']==current['cycle'] and row['endpoint']=='list_page_1':
                        current['list']=items;ready.set()
                except Exception as exc:emit('decode_failure',cycle=row['cycle'],error_type=type(exc).__name__)
            cdp.on('Network.requestWillBeSent',sent)
            cdp.on('Network.requestWillBeSentExtraInfo',request_headers)
            cdp.on('Network.responseReceived',response)
            cdp.on('Network.loadingFinished',lambda e:schedule(finished(e)))
            cdp.on('Page.domContentEventFired',lambda _:dom_ready.set())
            await cdp.send('Network.enable',{'maxTotalBufferSize':8000000,'maxResourceBufferSize':2000000})
            await cdp.send('Page.enable')
            emit('start',rounds=args.rounds,interval_seconds=args.interval,manual_seconds=args.manual_seconds,
                 identity='bloomberg-latest-lab-1',independent_sse=False,original_cache_disabled=False)
            stopped=False;started=time.monotonic()
            try:
                for n in range(args.rounds):
                    await asyncio.sleep(max(0,started+n*args.interval-time.monotonic()))
                    # Alternate ordering, so the second reload does not systematically favor one mode.
                    for disabled in ([False,True] if n%2==0 else [True,False]):
                        mode='cache_off' if disabled else 'cache_on'
                        current={'cycle':f'{n}:{mode}','mode':mode,'list':[],'responses':[]}
                        ready.clear();dom_ready.clear()
                        await cdp.send('Network.setCacheDisabled',{'cacheDisabled':disabled})
                        emit('reload_started',cycle=current['cycle'],mode=mode)
                        try:
                            await cdp.send('Page.reload',{'ignoreCache':disabled})
                            await asyncio.wait_for(dom_ready.wait(),20)
                            try:await asyncio.wait_for(ready.wait(),8)
                            except TimeoutError:pass
                            deadline=time.monotonic()+4
                            while True:
                                dom=await page.evaluate(DOM)
                                expected={r['url'] for r in current['list']}
                                visible={canonical(r['url']) for r in dom['rows']}
                                if dom['challenge'] or expected and expected<=visible or time.monotonic()>=deadline:break
                                await asyncio.sleep(.25)
                            rejected=any(r['status'] in {403,429} and r['endpoint'] in {'document','list_page_1'} for r in current['responses'])
                            emit('refresh_result',cycle=current['cycle'],mode=mode,**dom,
                                 validated_list_count=len(current['list']),
                                 api_urls_missing_from_dom=sorted(expected-visible),
                                 valid=bool(current['list']) and bool(dom['rows']) and not (expected-visible) and not dom['challenge'] and not rejected)
                            if rejected or dom['challenge']:
                                emit('stopped',reason='site_restriction_no_automatic_retry',cycle=current['cycle']);stopped=True;break
                            if n==0 and disabled and args.check_load_more:
                                before=len(dom['rows'])
                                try:
                                    await page.get_by_role('button',name='more stories',exact=True).click(timeout=10000)
                                    await page.wait_for_function('n=>document.querySelectorAll(\'[class*="Latest_latestBody"] a[class*="Latest_storyLink"]\').length>n',arg=before,timeout=10000)
                                    more=await page.evaluate(DOM);emit('pagination_check',before=before,after=len(more['rows']),rows=more['rows'])
                                except Exception as exc:
                                    emit('pagination_failed',error_type=type(exc).__name__,coverage_verified=False)
                        except Exception as exc:
                            emit('cycle_failed',cycle=current['cycle'],mode=mode,error_type=type(exc).__name__)
                            stopped=True;break
                    if stopped:break
                await cdp.send('Network.setCacheDisabled',{'cacheDisabled':False})
                current={'cycle':'manual_window','mode':'manual','list':[],'responses':[]}
                emit('manual_window_started',seconds=args.manual_seconds,automatic_reloads=False)
                end=time.monotonic()+args.manual_seconds
                previous=None
                while time.monotonic()<end:
                    dom=await page.evaluate(DOM)
                    signature=json.dumps(dom,sort_keys=True)
                    if signature!=previous:emit('manual_dom',**dom);previous=signature
                    await asyncio.sleep(2)
            finally:
                await cdp.send('Network.setCacheDisabled',{'cacheDisabled':False})
                if tasks:await asyncio.gather(*tasks,return_exceptions=True)
                await cdp.detach();emit('end',cache_behavior_restored=True,browser_preserved=True,stopped_for_restriction=stopped)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True);p.add_argument('--rounds',type=int,default=10)
    p.add_argument('--interval',type=int,default=60);p.add_argument('--manual-seconds',type=int,default=120)
    p.add_argument('--check-load-more',action='store_true');args=p.parse_args()
    if not 1<=args.rounds<=20 or args.interval<60 or not 0<=args.manual_seconds<=300:p.error('bounded rounds/interval required')
    asyncio.run(run(args))
