"""Trusted Reuters browser recipe shared by legacy and Site Access runtimes."""

from __future__ import annotations

from typing import Any, cast
import re
import json
import asyncio
import time
from urllib.parse import quote, urlsplit, parse_qs


def is_reuters_search_request(url: str, query: str, offset: int) -> bool:
    """Observe the query-bound request made by the native search form."""
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='www.reuters.com' or parsed.path!='/pf/api/v3/content/fetch/articles-by-search-v2':
            return False
        payload=json.loads(parse_qs(parsed.query).get('query',['{}'])[0])
        return payload.get('keyword')==query and int(payload.get('offset',0))==offset
    except (ValueError,TypeError,AttributeError):
        return False


SEARCH_STATE = r"""() => {
  const input=document.querySelector('main input');
  return {query:input?.value || '', offset:Number(new URLSearchParams(location.search).get('offset')||0),
    first:[...document.querySelectorAll('main a[href]')].find(a=>/-\d{4}-\d{2}-\d{2}\/$/.test(a.pathname))?.href};
}"""

SEARCH_READY = r"""({query,offset,previous}) => {
  const main=document.querySelector('main');
  const input=main?.querySelector('input');
  if (!main || input?.value.trim().toLowerCase() !== query.trim().toLowerCase()
      || Number(new URLSearchParams(location.search).get('offset')||0)!==offset) return false;
  const links=[...main.querySelectorAll('a[href]')].filter(a=>/-\d{4}-\d{2}-\d{2}\/$/.test(a.pathname));
  if (links.length) return !previous || links[0].href!==previous;
  const text=(main.innerText||'').toLowerCase();
  // Reuters initially paints an empty query shell. It is never a zero-result proof.
  return text.includes('no search results match the term') && text.includes(query.toLowerCase());
}"""


async def wait_for_native_document_redirect(page: Any, response: Any) -> Any:
    """Allow a JS verification document to finish its own navigation.

    This neither reloads nor interacts with a challenge. A human challenge or
    static denial stays a failure; only an observed successful main-document
    response can replace the initial HTTP status.
    """
    if response is None or response.status not in {401, 403}:
        return response
    html = (await page.content()).casefold()
    if "captcha-delivery.com" not in html and "please enable js" not in html:
        return response
    try:
        return await page.wait_for_event(
            "response",
            predicate=lambda item: (
                item.request.is_navigation_request()
                and item.frame == page.main_frame
                and 200 <= item.status < 300
            ),
            timeout=8_000,
        )
    except TimeoutError:
        return response
    except Exception as exc:
        # Playwright's timeout type is not Python's builtin TimeoutError.
        if type(exc).__name__ == "TimeoutError":
            return response
        raise


def native_search_rows(payload: object, *, empty_confirmed: bool = False) -> list[dict[str, object]]:
    """Parse the browser's own completed search response, never issue another request."""
    result=payload.get('result',{}) if isinstance(payload,dict) else {}
    articles=result.get('articles') if isinstance(result,dict) else None
    # A real empty search omits articles entirely. Accept it only alongside the
    # initialized query-specific empty DOM, never from missing API data alone.
    if articles is None and empty_confirmed and isinstance(result,dict) and isinstance(result.get('pagination'),dict):
        return []
    if not isinstance(articles,list):
        raise ValueError('invalid_reuters_search_response')
    rows=[]
    for article in articles:
        if not isinstance(article,dict):continue
        url=str(article.get('canonical_url') or '')
        parsed=urlsplit(url)
        stamp=re.search(r'-(\d{4}-\d{2}-\d{2})/$',parsed.path)
        title=str(article.get('title') or article.get('basic_headline') or '').strip()
        if not stamp or len(title)<15 or (parsed.hostname and parsed.hostname!='www.reuters.com'):
            continue
        published=article.get('published_time')
        rows.append({'url':url,'title':title,'date':published or stamp[1],
            'summary':str(article.get('description') or ''),'date_basis':'publisher_timestamp' if published else 'url_date'})
    if articles and not rows:
        raise ValueError('reuters_search_articles_not_parseable')
    return rows


async def capture_reuters_search(page: Any, query: str, offset: int) -> list[dict[str, object]]:
    page._doxagent_reuters_consumed = None
    responses=[];ready=asyncio.Event();navigation={}
    def observe(response):
        if is_reuters_search_request(response.url,query,offset):
            responses.append(response);ready.set()
    listening=hasattr(page,'on')
    if listening:page.on('response',observe)
    try:
        rows=await _capture_reuters_search_dom(page,query,offset,navigation=navigation)
        if listening and navigation.get('interactive'):
            async with asyncio.timeout(12):await ready.wait()
        if responses:
            response=responses[-1]
            if response.status!=200:
                error=RuntimeError('Reuters native search request rejected')
                error.status_code=response.status;error.response_url=page.url
                error.response_body=await page.content();error.response_headers={}
                raise error
            payload=await response.json()
            page._doxagent_reuters_consumed=(response.request,int(time.time()*1000))
            return native_search_rows(payload,empty_confirmed=not rows)
        return rows
    finally:
        if listening:page.remove_listener('response',observe)


def mark_consumed_search(page, tracked_requests, timings):
    """Retain all transport observations; only the response actually parsed is listing proof."""
    consumed=getattr(page,'_doxagent_reuters_consumed',None)
    if not consumed:return
    index=tracked_requests.get(id(consumed[0]))
    if index is None:return
    for other in tracked_requests.values():
        if other!=index:timings[f'request_{other}_auxiliary']=1
    timings.pop(f'request_{index}_auxiliary',None)
    timings.setdefault(f'request_{index}_body_epoch_ms',consumed[1])


async def _capture_reuters_search_dom(page: Any, query: str, offset: int, *, navigation: dict) -> list[dict[str, object]]:
    if not query.strip() or offset < 0 or offset % 20:
        raise ValueError('invalid_reuters_query_or_offset')
    current = await page.evaluate(SEARCH_STATE)
    current_url = urlsplit(page.url)
    reuse = (current_url.hostname == 'www.reuters.com' and current_url.path == '/site-search/'
             and bool(current.get('query')) and
             (current['query'] != query or current.get('offset',0) < offset))
    response = None if reuse else await page.goto(
        f"https://www.reuters.com/site-search/?query={quote(query)}&offset={offset}",
        # Third-party/deferred scripts can indefinitely postpone DCL even after
        # a complete native listing is rendered. The contract below waits for
        # actual result cards (or explicit zero results), not page-wide events.
        wait_until="commit",
    )
    response = await wait_for_native_document_redirect(page, response)
    status = response.status if response is not None else 200
    if status >= 400:
        error = RuntimeError(f"Reuters search returned HTTP {status}")
        error.status_code = status
        try:
            error.response_body = await page.content()
        except Exception:
            error.response_body = ""  # Diagnostic navigation races must not erase the known HTTP error.
        try:
            headers = await response.all_headers() if response else {}
        except Exception:
            headers = {}
        error.response_headers = {
            key: value
            for key, value in headers.items()
            if key.casefold() in {"content-type", "retry-after", "location", "server", "x-datadome"}
        }
        error.response_url = page.url
        raise error
    await page.locator('main input').first.wait_for(state='visible',timeout=12_000)
    current = await page.evaluate(SEARCH_STATE)
    if current.get('query') != query or current.get('offset',0) > offset:
        navigation['interactive']=True
        # A normal form submission initializes client search; no scripted fetch,
        # identity override, challenge interaction or guessed backend endpoint.
        await page.bring_to_front()
        field=page.locator('main input').first
        await field.fill(query)
        await field.press('Enter')
        await page.wait_for_function(SEARCH_READY,arg={'query':query,'offset':0,'previous':None},polling=250,timeout=12_000)
        current=await page.evaluate(SEARCH_STATE)
    while current.get('offset',0) < offset:
        navigation['interactive']=True
        previous=current.get('first')
        next_offset=current.get('offset',0)+20
        await page.bring_to_front()
        await page.get_by_role('button',name=re.compile(r'^Next stories')).click(timeout=5_000)
        await page.wait_for_function(SEARCH_READY,arg={'query':query,'offset':next_offset,'previous':previous},polling=250,timeout=12_000)
        current=await page.evaluate(SEARCH_STATE)
        if current.get('offset',0) != next_offset:
            raise RuntimeError('reuters_pagination_did_not_advance')
    await page.wait_for_function(
        SEARCH_READY, arg={'query':query,'offset':offset,'previous':None},
        polling=250,  # Native Chrome background tabs do not reliably run requestAnimationFrame.
        timeout=12_000,
    )
    rows = await page.evaluate(
        r"""() => {
          const months = '(?:January|February|March|April|May|June|July|August|'
            + 'September|October|November|December)';
          const pattern = new RegExp(months + '\\s+\\d{1,2},\\s+\\d{4}');
          const out = [], seen = new Set();
          for (const link of document.querySelectorAll('main a[href]')) {
            const href = link.getAttribute('href') || '';
            const title = (link.textContent || '').trim();
            const articlePath = /\/[^/]+\/[^/]+-\d{4}-\d{2}-\d{2}\//;
            if (!title || title.length < 15 || !href.startsWith('/')
                || !articlePath.test(href) || seen.has(href)) continue;
            let node = link, card = null;
            for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
              if (node.tagName === 'MAIN' || node.tagName === 'BODY') break;
              const articles = new Set([...node.querySelectorAll('a[href]')]
                .map(a => a.getAttribute('href')).filter(h => articlePath.test(h || '')));
              if (articles.size > 1) break;
              if (articles.size === 1) card = node;
              if (card && node.matches('article, li, [data-testid*="card"]')) break;
            }
            const text = (card?.innerText || card?.textContent || '').replace(/\s+/g, ' ').trim();
            const match = text.match(pattern);
            const urlDate = href.match(/-(\d{4}-\d{2}-\d{2})\/$/);
            const publishedDate = urlDate?.[1] || match?.[0];
            if (!publishedDate) continue;
            seen.add(href);
            const summaryNode = card?.querySelector(
              '[data-testid*="description"], [data-testid*="summary"], p');
            let summary = (summaryNode?.textContent || '').trim();
            const relativePattern = /\b\d+\s+(?:mins?|minutes?|hours?)\s+ago\b/i;
            if (summary === title || (summary.length < 80
                && (relativePattern.test(summary) || pattern.test(summary)))) summary = '';
            const relative = text.match(relativePattern)?.[0];
            out.push({url: href, title, date: publishedDate, summary,
              date_basis: urlDate ? 'url_date' : 'card_date',
              card_date: match?.[0] || null, relative_time: relative || null});
          }
          return out;
        }"""
    )
    return cast(list[dict[str, object]], rows)


__all__ = ["capture_reuters_search", "wait_for_native_document_redirect"]
