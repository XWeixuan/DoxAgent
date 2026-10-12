"""Finite HTTPS GET controls; no egress mutation or browser control."""
import asyncio, json, time
from datetime import UTC, datetime
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token

async def main():
    out = {"observed_at": datetime.now(UTC).isoformat()}
    s = DoxAgentSettings()
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011", timeout=15, headers={"Authorization": "Bearer " + read_token(s.site_access_admin_token, s.site_access_admin_token_file)}) as c:
        out["egresses"] = (await c.get("/v1/egresses")).json()
    routes = {"direct": None, "us-standard-5": "http://doxagent-egress-clash:18081", "nl-standard-2": "http://doxagent-egress-clash:18082", "DE-slot": "http://doxagent-egress-clash:18083", "jp-standard-6": "http://doxagent-egress-clash:18080", "server-direct-listener": "http://doxagent-egress-clash:18084"}
    async def get(name, proxy, url):
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=8, follow_redirects=True) as c:
                r = await c.get(url)
                return {"route": name, "url": url, "status": r.status_code, "seconds": round(time.monotonic()-start,2), "server": r.headers.get("server"), "content_type": r.headers.get("content-type"), "preview": r.text[:180]}
        except Exception as e:
            return {"route": name, "url": url, "error": type(e).__name__, "message": str(e)[:160], "seconds": round(time.monotonic()-start,2)}
    out["controls"] = await asyncio.gather(*(get(k,v,"https://api.ipify.org?format=json") for k,v in routes.items()))
    urls = ("https://www.digitimes.com.tw/tech/rss/xml/xmlrss_10_40.xml", "https://www.globenewswire.com/RssFeed/industry/9576-Semiconductors/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Semiconductors")
    out["feed_controls"] = await asyncio.gather(*(get(k,routes[k],url) for k in ("direct","DE-slot") for url in urls))
    print(json.dumps(out,ensure_ascii=False),flush=True)
asyncio.run(main())
