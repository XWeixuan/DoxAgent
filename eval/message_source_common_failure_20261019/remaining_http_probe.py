"""Bounded transport checks for residual non-browser failures; no publishing."""
import asyncio, json, sqlite3, time
import httpx
from doxagent.settings import DoxAgentSettings


async def main():
    s=DoxAgentSettings()
    d=sqlite3.connect('file:'+str(s.message_bus_v2_sqlite_path)+'?mode=ro',uri=True)
    row=d.execute("SELECT data_json FROM ticker_source_bindings WHERE binding_id='CEG:newswire_rss'").fetchone()
    feeds=json.loads(row[0])['source_parameters'].get('rss_urls',[]) if row else []
    async def feed(url,route,proxy):
        start=time.monotonic()
        try:
            async with httpx.AsyncClient(proxy=proxy,timeout=8,follow_redirects=True) as c:
                r=await c.get(url)
                print(json.dumps(dict(kind='CEG_RSS',route=route,status=r.status_code,xml='<rss' in r.text or '<feed' in r.text,bytes=len(r.content),seconds=round(time.monotonic()-start,2))),flush=True)
        except Exception as e:
            print(json.dumps(dict(kind='CEG_RSS',route=route,error=type(e).__name__)),flush=True)
    await asyncio.gather(*(feed(url,route,proxy) for url in feeds[:2] for route,proxy in [('direct',None),('de-standard-1','http://doxagent-egress-clash:18083')]))
    async with httpx.AsyncClient(timeout=12) as c:
        r=await c.get(s.tikhub_base_url.rstrip('/')+'/api/v1/twitter/web/fetch_user_post_tweet',params={'screen_name':'MicronTech'},headers={'Authorization':'Bearer '+s.tikhub_api_key})
        try:
            payload=r.json()
            detail={k:payload[k] for k in ['code','message','msg','detail'] if k in payload}
            safe=json.dumps(detail).replace(s.tikhub_api_key,'[redacted]')[:700]
        except ValueError:
            safe='non_json'
        print(json.dumps(dict(kind='TikHub',status=r.status_code,detail=safe)),flush=True)

asyncio.run(main())
