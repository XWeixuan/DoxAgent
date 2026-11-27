"""Read-only website reconnaissance; raw public responses remain private."""

import asyncio
import json
import os
from pathlib import Path
from lxml import html
from doxagent.message_bus_v2.technology_sources import TechnologyHttp, TechnologySource

TARGETS = {
    "sec_crypto": "https://www.sec.gov/featured-topics/crypto-task-force/cryptosec",
    "sec_press": "https://www.sec.gov/newsroom/press-releases",
    "markets_exchanges": "https://www.marketsmedia.com/category/exchanges-sectors/",
    "markets_from": "https://www.marketsmedia.com/category/from-the-markets/",
    "sifma_letters": "https://www.sifma.org/advocacy/letters",
    "sifma_press": "https://www.sifma.org/news/press-releases",
    "ice": "https://ir.theice.com/press/press-releases/default.aspx",
    "dtcc": "https://www.dtcc.com/press-releases",
    "tzero": "https://www.tzero.com/news",
    "securitize": "https://investors.securitize.io/news/",
}


async def main():
    from urllib.parse import urlsplit

    os.umask(0o077)
    directory = Path("/tmp/secz-probe")
    directory.mkdir(exist_ok=True)
    http = TechnologyHttp()
    slots = asyncio.Semaphore(3)

    async def one(key, url):
        async with slots:
            spec = TechnologySource(
                "secz_probe_" + key, key, urlsplit(url).hostname, (url,), (), route="direct"
            )
            result = {"key": key, "url": url}
            try:
                c = await http.fetch(url, spec)
                (directory / (key + ".html")).write_text(c.text)
                result.update(
                    status=c.status,
                    final_url=c.url,
                    chars=len(c.text),
                    headers={
                        k: c.headers.get(k)
                        for k in [
                            "age",
                            "cache-control",
                            "etag",
                            "last-modified",
                            "content-type",
                            "date",
                        ]
                    },
                )
                if c.status == 200 and "<" in c.text[:50]:
                    d = html.fromstring(c.text)
                    result.update(
                        title=d.xpath("//title/text()")[:1], scripts=d.xpath("//script/@src")[-16:]
                    )
            except Exception as e:
                result["error"] = type(e).__name__ + ":" + str(e)[:180]
            print(json.dumps(result), flush=True)

    try:
        await asyncio.gather(*(one(k, u) for k, u in TARGETS.items()))
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
