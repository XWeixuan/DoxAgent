"""Read-only, bounded same-window Yahoo listing comparison; no Bus writes."""
import concurrent.futures
import json
import sys
import time
import os
from datetime import UTC, datetime
from pathlib import Path

from curl_cffi import requests
from lxml import html

QUERY = """query GetQSPLeafNewsStream($listInput:LightyearListInput!,$clientContext:ClientContext!,$start:Int!,$count:Int!) {
 lightyearList(list_input:$listInput,cc:$clientContext,start:$start,count:$count) {
 main:contentItems {stream:assets {asset {__typename
 ... on Story {id:uuid title contentAttributes {pubDate:publishTime displayTime canonicalUrl clickthroughUrl(cc:$clientContext) summary provider {displayName url providerContentUrl}} finance {stockTickers {symbol}}}
 ... on Video {id:uuid title contentAttributes {pubDate:publishTime displayTime canonicalUrl clickthroughUrl(cc:$clientContext) summary provider {displayName url providerContentUrl}} finance {stockTickers {symbol}}}
 ... on Outlink {uuid headline displayTime url description provider {displayName url providerContentUrl}}
 }} pageInfo {totalCount start count nextPage:hasNextPage}}}}
"""


def variables(ticker, alias="all", count=50):
    return dict(clientContext=dict(device="desktop", region="US", site="finance", lang="en-US"),
                listInput=dict(slug="list=finance-US-en-US-ticker-" + alias,
                               queryVariables=dict(tickerSymbol=[ticker]), assetTypes=["story", "video"],
                               disableDedupe=False, enableBlockedContent=False, filterClientContext=False),
                start=0, count=count)


def probe(ticker):
    result = dict(ticker=ticker, observed_at=datetime.now(UTC).isoformat())
    proxy = os.environ.get('YAHOO_PROBE_PROXY')
    if proxy == 'configured':
        from doxagent.settings import DoxAgentSettings
        settings = DoxAgentSettings()
        proxy = settings.message_bus_v2_yahoo_http_proxy_url or settings.crawler_egress_proxy_url
    with requests.Session(impersonate="chrome", trust_env=False, proxy=proxy or '') as s:
        urls = [("graphql", "https://nexus-finance-prod.media.yahoo.com/"),
                ("html", f"https://finance.yahoo.com/quote/{ticker}/latest-news/"),
                ("rss", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US")]
        for kind, url in urls:
            started = time.monotonic()
            try:
                r = (s.post(url, json=dict(query=QUERY, variables=variables(ticker)),
                            headers={"x-yahoo-cg-client-name": "finance"}, timeout=10)
                     if kind == "graphql" else s.get(url, timeout=10))
                data = dict(status=r.status_code, seconds=round(time.monotonic() - started, 3),
                            bytes=len(r.content), observed_at=datetime.now(UTC).isoformat(),
                            cache={k: r.headers.get(k) for k in ("date", "age", "cache-control", "etag")})
                if r.status_code == 200:
                    (OUT / f"{ticker}-{kind}.txt").write_text(r.text, encoding="utf-8")
                    if kind == "graphql":
                        payload = r.json()
                        data["errors"] = payload.get("errors")
                        main = payload.get("data", {}).get("lightyearList", {}).get("main", {})
                        data["pageInfo"] = main.get("pageInfo")
                        data["rows"] = [dict(id=a.get("id") or a.get("uuid"), title=a.get("title") or a.get("headline"),
                                             time=(a.get("contentAttributes") or {}).get("pubDate") or a.get("displayTime"),
                                             url=(a.get("contentAttributes") or {}).get("canonicalUrl") or a.get("url"))
                                        for n in main.get("stream", []) if (a := n.get("asset"))]
                    elif kind == "html":
                        d = html.fromstring(r.text)
                        data["rows"] = [dict(text=" ".join(c.text_content().split()),
                                             links=c.xpath('.//a[@href][.//h2 or .//h3]/@href'))
                                        for c in d.xpath('//*[@data-testid="storyitem"]')]
                    else:
                        from xml.etree import ElementTree as ET
                        data["rows"] = [dict(title=n.findtext("title"), url=n.findtext("link"), time=n.findtext("pubDate"))
                                        for n in ET.fromstring(r.text).findall("./channel/item")]
                result[kind] = data
            except Exception as e:
                result[kind] = dict(error=type(e).__name__, detail=str(e)[:140], seconds=round(time.monotonic() - started, 3))
    return result


if __name__ == "__main__":
    OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
    tickers = sys.argv[2:] or ["INTC", "MU", "COHR", "NBIS", "BE", "CEG", "SNOW", "RKLB", "ONDS", "IONQ"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(probe, tickers))
    (OUT / "comparison.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{**{k: v for k, v in r.items() if k in {"ticker", "observed_at"}},
                       **{k: {f: v for f, v in d.items() if f != "rows"} | {"count": len(d.get("rows", []))}
                          for k, d in r.items() if isinstance(d, dict)}} for r in results], ensure_ascii=False))
