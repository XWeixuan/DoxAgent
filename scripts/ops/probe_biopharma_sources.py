"""Read-only official entry captures; private HTML stays outside version control."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from doxagent.message_bus_v2.technology_sources import TechnologyHttp, TechnologySource

ENTRIES = {
    "fda.approvals": "https://www.fda.gov/drugs/news-events-human-drugs/notable-approvals-drugs",
    "fda.biologics": "https://www.fda.gov/vaccines-blood-biologics/news-events-biologics/whats-new-biologics",
    "fda.calendar": "https://www.fda.gov/advisory-committees/advisory-committee-calendar",
    "endpoints": "https://endpoints.news/news/",
    "fierce": "https://www.fiercebiotech.com/biotech",
    "stat.biotech": "https://www.statnews.com/topic/biotech/",
    "stat.trials": "https://www.statnews.com/topic/clinical-trials/",
    "hkex": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
    "akeso": "https://akesobio.com/en/media/akeso-news",
    "akeso.pr": "https://www.prnewswire.com/news/akeso%2C-inc./",
    "taiho": "https://www.taihooncology.com/us/news/",
    "ash": "https://onlineprogram.hematology.org/",
    "aes": "https://aesnet.org/education/annual-meeting/aes-abstract-search",
    "xene.ir": "https://investor.xenon-pharma.com/news-events/press-releases",
    "rgnx.ir": "https://ir.regenxbio.com/press-releases",
    "prax.ir": "https://investor.praxismedicines.com/press-releases",
    "cgem.ir": "https://investors.cullinantherapeutics.com/news-releases/",
    "smmt.ir": "https://smmttx.com/news/press-releases/default.aspx",
}


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--route", default="direct")
    p.add_argument("--method", default="GET")
    p.add_argument("--entry", action="append")
    p.add_argument("--targets", type=Path)
    p.add_argument("--referer")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    http = TechnologyHttp()
    slots = asyncio.Semaphore(3)

    async def one(key, url):
        async with slots:
            result = {
                "entry": key,
                "url": url,
                "at": datetime.now(UTC).isoformat(),
                "route": args.route,
                "method": args.method,
            }
            spec = TechnologySource(
                "biopharma_probe", key, urlsplit(url).hostname, (url,), (), route=args.route
            )
            try:
                c = await http.fetch(
                    url,
                    spec,
                    method=args.method,
                    headers={"Referer": args.referer} if args.referer else None,
                )
                (args.out / (key + ".private.html")).write_text(c.text, encoding="utf-8")
                result.update(
                    status=c.status,
                    final_url=c.url,
                    bytes=len(c.text.encode()),
                    headers={
                        k: c.headers.get(k)
                        for k in ("date", "age", "cache-control", "etag", "last-modified")
                    },
                )
            except Exception as exc:
                result["error"] = type(exc).__name__ + ":" + str(exc)[:220]
            print(json.dumps(result), flush=True)

    try:
        entries = json.loads(args.targets.read_text()) if args.targets else ENTRIES
        await asyncio.gather(
            *(one(k, v) for k, v in entries.items() if not args.entry or k in args.entry)
        )
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
