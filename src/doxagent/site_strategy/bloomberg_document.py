"""Read the article already rendered by the authorized Chrome context.

This observes visibility; it never unhides text, changes entitlement, or makes
additional publisher requests. Snapshot markers are internal quality evidence.
"""

import asyncio
import json
import time
from html import escape


_READ = r"""() => {
 const visible = e => {
   if (!e.getClientRects().length) return false;
   const rect=e.getBoundingClientRect();
   for(let n=e;n && n!==document.documentElement;n=n.parentElement){
     const s=getComputedStyle(n);
     if(n.hidden || n.getAttribute('aria-hidden')==='true' || s.display==='none'
        || s.visibility==='hidden' || Number(s.opacity)===0) return false;
     if(n!==e && n!==document.body && /hidden|clip/.test(s.overflowY)) {
       const r=n.getBoundingClientRect();
       if(rect.top < r.top-2 || rect.bottom > r.bottom+2) return false;
     }
   }
   return true;
 };
 const article=document.querySelector('article.current[data-story-id]')
   || document.querySelector('article[data-story-id]');
 const body=article?.querySelector('.body-content');
 if(!body) return null;
 const ps=[...body.querySelectorAll('p')];
 const paid=ps.filter(p=>p.classList.contains('paywall'));
 const wall=[...article.querySelectorAll('[class*="fence"], [class*="paywall"]')]
   .some(e=>e.tagName!=='P' && visible(e) && e.querySelector('a,button')
     && /subscribe to|sign in to|already a subscriber|unlock this/i.test(e.innerText));
 const schemas=[...document.querySelectorAll('script[type="application/ld+json"]')]
   .flatMap(s=>{try{let v=JSON.parse(s.textContent);return Array.isArray(v)?v:[v]}catch{return []}})
   .flatMap(v=>v['@graph']||[v]);
 const schema=schemas.find(v=>v['@type']==='NewsArticle')||{};
 const free=schema.isAccessibleForFree===true || schema.isAccessibleForFree==='true';
 const allVisible=ps.length>0 && visible(body) && ps.every(visible);
 const complete=allVisible && !wall && (free || paid.length>0);
 const clone=body.cloneNode(true);
 const originals=[body,...body.querySelectorAll('*')], copies=[clone,...clone.querySelectorAll('*')];
 for(let i=copies.length-1;i>0;i--) {
   if(!visible(originals[i]) || /^(SCRIPT|STYLE|FORM|NAV|ASIDE|IFRAME)$/.test(copies[i].tagName)) copies[i].remove();
 }
 // Keep text, links, tables and lists; discard application attributes/handlers.
 for(const e of [clone,...clone.querySelectorAll('*')]) {
   for(const a of [...e.attributes]) if(!['href','colspan','rowspan'].includes(a.name)) e.removeAttribute(a.name);
 }
 clone.className='body-content';
 return {storyId:article.getAttribute('data-story-id'), canonical:document.querySelector('link[rel="canonical"]')?.href,
   headline:article.querySelector('h1')?.innerText || document.querySelector('h1')?.innerText || '',
   schema:{'@type':'NewsArticle',isAccessibleForFree:free,datePublished:schema.datePublished,dateModified:schema.dateModified},
   body:clone.outerHTML, text:body.innerText, paragraphs:ps.length, paidParagraphs:paid.length,
   visibleParagraphs:ps.filter(visible).length, complete};
}"""


def snapshot(data):
    """Encode only observed article content, excluding account UI and state."""
    esc = lambda value: escape(str(value or ''), quote=True)
    schema = json.dumps(data['schema']).replace('<', '\\u003c')
    return (
        '<html><head><link rel="canonical" href="' + esc(data.get('canonical')) + '">'
        '<script type="application/ld+json">' + schema + '</script></head><body>'
        '<article data-story-id="' + esc(data['storyId']) + '" '
        'data-dox-bloomberg-snapshot="visible-v1" data-dox-bloomberg-complete="'
        + str(data['complete']).lower() + '"><h1>' + esc(data['headline']) + '</h1>'
        + data['body'] + '</article></body></html>'
    )


async def capture_document(page, *, timeout=12):
    deadline = time.monotonic() + timeout
    previous = None
    latest = None
    while True:
        latest = await page.evaluate(_READ)
        if latest:
            signature = (latest['storyId'], latest['text'], latest['complete'])
            if latest['complete'] and signature == previous:
                return snapshot(latest), page.url
            previous = signature
        if time.monotonic() >= deadline:
            return (snapshot(latest) if latest else await page.content()), page.url
        await asyncio.sleep(min(.4, max(.001, deadline - time.monotonic())))
