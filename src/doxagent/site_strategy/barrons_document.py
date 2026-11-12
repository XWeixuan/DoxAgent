"""Observe the current article's rendered paragraphs, including paid visible DOM.

The publisher's `crawler` class is also present on authorized visible bodies.
It is not an entitlement verdict. Never unhide text or read account/Cookie state.
"""
import asyncio
import json
import time
from html import escape

READ = r"""() => {
 const visible = e => {
   if(!e.getClientRects().length) return false;
   const rect=e.getBoundingClientRect();
   for(let n=e;n && n!==document.documentElement;n=n.parentElement){
     const s=getComputedStyle(n);
     if(n.hidden || n.getAttribute('aria-hidden')==='true' || s.display==='none'
        || s.visibility==='hidden' || Number(s.opacity)===0) return false;
     if(n!==e && n!==document.body && /hidden|clip/.test(s.overflowY)) {
       const r=n.getBoundingClientRect();
       if(rect.top<r.top-2 || rect.bottom>r.bottom+2) return false;
     }
   }
   return true;
 };
 const slug=decodeURIComponent(location.pathname).split('/').filter(Boolean).pop();
 const article=document.getElementById(slug);
 if(!article || article.tagName!=='ARTICLE') return null;
 const ps=[...article.querySelectorAll('[data-type="paragraph"]')]
   .filter(p=>p.closest('article')===article);
 if(!ps.length) return null;
 const paid=ps.filter(p=>p.closest('.paywall'));
 const gate=[...article.querySelectorAll('div,section,form')].some(e=>visible(e)
   && (e.innerText||'').length<700 && e.querySelector('a,button,input[type=password]')
   && /subscribe to (?:continue|read|unlock)|sign in to (?:continue|read)|already a subscriber/i.test(e.innerText));
 const schemas=[...document.querySelectorAll('script[type="application/ld+json"]')]
   .flatMap(s=>{try{let v=JSON.parse(s.textContent);return Array.isArray(v)?v:[v]}catch{return []}})
   .flatMap(v=>v['@graph']||[v]);
 const schema=schemas.find(v=>/Article/.test(String(v['@type'])))||{};
 const free=schema.isAccessibleForFree===true || schema.isAccessibleForFree==='true';
 const complete=ps.every(visible) && !gate && (free || paid.length>0);
 return {id:slug,canonical:document.querySelector('link[rel=canonical]')?.href,
   headline:article.querySelector('h1')?.innerText || document.querySelector('h1')?.innerText || '',
   paragraphs:ps.filter(visible).map(p=>p.innerText.trim()).filter(Boolean),
   count:ps.length,visibleCount:ps.filter(visible).length,paidCount:paid.length,complete,
   free,published:schema.datePublished,modified:schema.dateModified};
}"""


def snapshot(value):
    esc=lambda v:escape(str(v or ''),quote=True)
    schema={'@type':'NewsArticle','headline':value['headline'],
            'isAccessibleForFree':value['free'],'datePublished':value.get('published'),
            'dateModified':value.get('modified')}
    return ('<html><head><link rel="canonical" href="'+esc(value.get('canonical'))+'">'
        '<script type="application/ld+json">'+json.dumps(schema).replace('<','\\u003c')+'</script>'
        '</head><body><article id="'+esc(value['id'])+'" data-dox-barrons-visible="v1" '
        'data-dox-barrons-complete="'+str(value['complete']).lower()+'"><h1>'+esc(value['headline'])+'</h1>'
        +''.join('<p data-type="paragraph">'+esc(p)+'</p>' for p in value['paragraphs'])
        +'</article></body></html>')


async def capture_document(page, *, timeout=12):
    deadline=time.monotonic()+timeout
    previous=None
    while True:
        value=await page.evaluate(READ)
        if value:
            signature=(value['id'],value['paragraphs'],value['complete'])
            if value['complete'] and signature==previous:
                return snapshot(value)
            previous=signature
        if time.monotonic()>=deadline:
            return snapshot(value) if value else await page.content()
        await asyncio.sleep(min(.4,max(.001,deadline-time.monotonic())))
