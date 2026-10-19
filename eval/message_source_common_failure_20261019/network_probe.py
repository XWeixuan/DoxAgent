"""Bounded real HTTP probes from the affected process namespace; no publishing."""
import asyncio
import json
import os
import socket
import time

import httpx


async def main():
    print(json.dumps({'proxy_env':{k:v for k,v in os.environ.items() if k.lower() in
                    ('http_proxy','https_proxy','all_proxy','no_proxy')},
                    'resolv':open('/etc/resolv.conf').read()}),flush=True)
    for host in ('v2-site-access','doxagent-egress-clash','www.trendforce.com','huggingnews.com','www.thelec.kr'):
        try:
            print(json.dumps({'dns_host':host,'addresses':list({r[4][0] for r in socket.getaddrinfo(host,None)})}),flush=True)
        except Exception as e:print(json.dumps({'dns_host':host,'error':type(e).__name__}),flush=True)
    async def probe(url,trust_env):
        start=time.monotonic()
        try:
            async with httpx.AsyncClient(trust_env=trust_env,timeout=8,follow_redirects=True) as client:
                async with client.stream('GET',url) as response:
                    prefix=b''
                    async for block in response.aiter_bytes():
                        prefix+=block
                        if len(prefix)>=2048:break
                    result={'status':response.status_code,'bytes_prefix':len(prefix),'content_type':response.headers.get('content-type')}
        except Exception as e:result={'error':type(e).__name__,'cause':type(e.__cause__).__name__ if e.__cause__ else None}
        print(json.dumps({'url':url,'trust_env':trust_env,'seconds':time.monotonic()-start,**result}),flush=True)
    await asyncio.gather(*(probe(url,env) for url in ['http://v2-site-access:8011/readyz',
                'https://huggingnews.com/feed.xml','https://www.thelec.kr/rss/S1N2.xml',
                'https://www.trendforce.com/news/'] for env in (False,True)))


asyncio.run(main())
