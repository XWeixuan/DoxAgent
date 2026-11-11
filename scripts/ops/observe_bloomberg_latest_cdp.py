"""Observe one existing Bloomberg Latest tab; never navigate/login, spoof or scrape bodies.

Run inside Site Access against a registered lab maintenance session. Output contains
news metadata only, never headers, cookies, auth responses or arbitrary frame data.
"""
import argparse
import asyncio
import base64
import codecs
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ID = re.compile(r'^[A-Z0-9]{14}$')


def now():
    return datetime.now(UTC).isoformat()


def route(url):
    p = urlsplit(url)
    if p.hostname not in {'www.bloomberg.com', 'bloomberg.com'}:
        return None
    if p.path.startswith('/artemis/oscillator/stream'):
        return 'page_sse'
    if p.path.startswith('/artemis/oscillator/poll/'):
        return 'page_snapshot'
    if p.path.startswith('/lineup-next/api/stories'):
        return 'page_stories'
    return None


def articles(data):
    if isinstance(data, list):
        for value in data:
            yield from articles(value)
    elif isinstance(data, dict):
        identifier = data.get('id')
        if isinstance(identifier, str) and ID.fullmatch(identifier) and isinstance(data.get('publishedAt'), str):
            try:
                published = datetime.fromisoformat(data['publishedAt'].replace('Z', '+00:00'))
                if published.tzinfo is None:
                    return
            except (ValueError, TypeError):
                return
            yield {k: data.get(k) for k in ['id', 'publishedAt', 'headline', 'url', 'slug']}
        else:
            for key, value in data.items():
                if key in {'data', 'value', 'topicsData', 'LATEST_STORIES', 'items'}:
                    yield from articles(value)


class SSE:
    def __init__(self):
        self.buffer = ''
        self.decoder = codecs.getincrementaldecoder('utf-8')()

    def feed(self, data):
        self.buffer += self.decoder.decode(data)
        self.buffer = self.buffer.replace('\r\n', '\n')
        records = []
        while '\n\n' in self.buffer:
            chunk, self.buffer = self.buffer.split('\n\n', 1)
            payload = '\n'.join(line[5:].lstrip(' ') for line in chunk.split('\n') if line.startswith('data:'))
            if payload:
                try:
                    value = json.loads(payload)
                    if (isinstance(value, dict) and value.get('providerName') == 'STORIES_SESSION'
                            and isinstance(value.get('data'), dict)
                            and value['data'].get('name') == 'LATEST_STORIES'):
                        records.extend(articles(value['data']))
                except ValueError:
                    pass
        if len(self.buffer) > 262144:
            raise ValueError('unparsed_stream_exceeded_bound')
        return records


async def run(args):
    from playwright.async_api import async_playwright
    from doxagent.settings import DoxAgentSettings
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.governance_repository import GovernanceRepository
    from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
    settings = DoxAgentSettings()
    repo = SiteStrategyRepository(settings.site_access_sqlite_path)
    sessions = [s for s in GovernanceRepository(repo).list_sessions()
                if s.identity_id == args.identity and s.state in {'OPEN', 'RELEASED'}]
    if len(sessions) != 1:
        raise ValueError('require_exactly_one_existing_lab_session')
    status = await ChromeSupervisorClient(settings.site_access_chrome_supervisor_socket,
                  owner_id='bloomberg-passive-observer').status(args.identity)
    if not status.get('running'):
        raise ValueError('existing_chrome_not_running')
    with Path(args.output).open('a', encoding='utf-8', buffering=1) as output:
        def emit(kind, **data):
            output.write(json.dumps({'kind': kind, 'observed_at': now(), **data}, ensure_ascii=True) + '\n')
        seen, initial_routes, requests, streams, tasks = set(), set(), {}, {}, set()
        started_at = datetime.now(UTC)
        def observe(lane, rows):
            rows = list(rows)
            if not rows:
                return
            first_batch = lane not in initial_routes
            initial_routes.add(lane)
            for row in rows:
                baseline = first_batch and datetime.fromisoformat(row['publishedAt'].replace('Z', '+00:00')) < started_at
                key = lane, row['id']
                if key not in seen:
                    seen.add(key)
                    emit('article', route=lane, baseline=baseline, **row)
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(status['cdp_websocket_url'])
            pages = [p for c in browser.contexts for p in c.pages
                     if urlsplit(p.url).hostname in {'bloomberg.com', 'www.bloomberg.com'}
                     and urlsplit(p.url).path.rstrip('/') == '/latest']
            if len(pages) != 1:
                raise ValueError('require_exactly_one_existing_latest_tab')
            page = pages[0]
            cdp = await page.context.new_cdp_session(page)
            def schedule(coroutine):
                task = asyncio.create_task(coroutine)
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            async def response(event):
                r = event['response']; lane = route(r['url'])
                if not lane:
                    return
                request = event['requestId']
                requests[request] = (lane, r.get('status'))
                emit('response', route=lane, request_id=request, status=r.get('status'),
                     mime=r.get('mimeType'), from_disk_cache=r.get('fromDiskCache', False))
                if lane == 'page_sse' and r.get('status') == 200 and args.tap_stream:
                    streams[request] = SSE()
                    try:
                        buffered = await cdp.send('Network.streamResourceContent', {'requestId': request})
                        data = base64.b64decode(buffered.get('bufferedData', ''))
                        emit('stream_chunk', request_id=request, bytes=len(data), buffered=True)
                        observe(lane, streams[request].feed(data))
                        emit('stream_attached', request_id=request)
                    except Exception as exc:
                        emit('observer_error', phase='stream_attach', error_type=type(exc).__name__)
            async def finished(event):
                request = event['requestId']; lane, status_code = requests.pop(request, (None, None))
                if not lane:
                    return
                emit('request_finished', route=lane, request_id=request)
                if lane != 'page_sse' and status_code == 200:
                    try:
                        result = await cdp.send('Network.getResponseBody', {'requestId': request})
                        body = result.get('body', '')
                        if result.get('base64Encoded'):
                            body = base64.b64decode(body).decode('utf-8')
                        observe(lane, articles(json.loads(body)))
                    except Exception as exc:
                        emit('observer_error', phase='metadata_body', error_type=type(exc).__name__)
                streams.pop(request, None)
            def received(event):
                request = event['requestId']
                if request in streams and event.get('data'):
                    try:
                        data = base64.b64decode(event['data'])
                        emit('stream_chunk', request_id=request, bytes=len(data), buffered=False)
                        observe('page_sse', streams[request].feed(data))
                    except Exception as exc:
                        emit('observer_error', phase='stream_data', error_type=type(exc).__name__)
            def failed(event):
                request = event['requestId']; lane, _ = requests.pop(request, (None, None))
                if lane:
                    emit('request_failed', route=lane, request_id=request,
                         canceled=event.get('canceled'), blocked_reason=event.get('blockedReason'),
                         error_text=event.get('errorText'))
                    streams.pop(request, None)
            cdp.on('Network.responseReceived', lambda e: schedule(response(e)))
            cdp.on('Network.loadingFinished', lambda e: schedule(finished(e)))
            cdp.on('Network.loadingFailed', failed)
            cdp.on('Network.dataReceived', received)
            await cdp.send('Network.enable', {'maxTotalBufferSize': 8000000, 'maxResourceBufferSize': 2000000})
            emit('start', identity=args.identity, duration_seconds=args.seconds,
                 target_id=sessions[0].target_id, browser_navigation=False, stream_tap=args.tap_stream,
                 disclaimer='No authenticated/subscription assertion; initial old rows are baseline, compare exact publish times against start')
            deadline = asyncio.get_running_loop().time() + args.seconds
            last_state = None
            try:
                while asyncio.get_running_loop().time() < deadline:
                    state = await page.evaluate('''()=>({title:document.title,visibility:document.visibilityState,path:location.pathname,challenge:!!document.querySelector('#px-captcha,.px-captcha'),links:[...document.querySelectorAll('a[href*="/news/articles/"],a[href*="/opinion/articles/"],a[href*="/features/"],a[href*="/graphics/"],a[href*="/newsletters/"]')].filter(a=>a.textContent.trim()).slice(0,150).map(a=>({url:a.href.split('?')[0],headline:a.textContent.trim().slice(0,500)}))})''')
                    signature = (state['title'],state['visibility'],state['path'],state['challenge'])
                    if signature != last_state:
                        emit('page_state', **{k:v for k,v in state.items() if k!='links'})
                        last_state = signature
                    baseline = 'dom' not in initial_routes
                    initial_routes.add('dom')
                    for row in state['links']:
                        p=urlsplit(row['url'])
                        if p.hostname not in {'www.bloomberg.com','bloomberg.com'}:
                            continue
                        url=urlunsplit((p.scheme,p.netloc,p.path,'',''))
                        key='dom',url
                        if key not in seen:
                            seen.add(key);emit('dom_article',baseline=baseline,url=url,headline=row['headline'])
                    await asyncio.sleep(2)
            finally:
                await cdp.detach()
                if tasks:
                    await asyncio.gather(*tasks,return_exceptions=True)
                emit('end',observations=len(seen),browser_preserved=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--identity',default='bloomberg-latest-lab-1',choices=['bloomberg-latest-lab-1'])
    p.add_argument('--seconds',type=int,default=600)
    p.add_argument('--output',required=True)
    p.add_argument('--tap-stream', action='store_true', help='Explicitly enable experimental CDP fetch-SSE byte observation')
    args=p.parse_args()
    if not 10 <= args.seconds <= 1800:
        p.error('--seconds must be between10 and1800')
    asyncio.run(run(args))
