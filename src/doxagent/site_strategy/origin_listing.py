"""Read fresh publisher lists through an already verified browser origin."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from doxagent.content_enrichment.quality import inspect_html


async def fetch(page, url, *, budget_ms, max_bytes):
    if urlsplit(page.url).netloc != urlsplit(url).netloc:
        raise ValueError("listing_origin_mismatch")
    return await page.evaluate(
        """async ({url,budget,maxBytes}) => {
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),budget);
      try {
        const r=await fetch(url,{credentials:'include',cache:'no-cache',signal:controller.signal});
        const text=await r.text();
        if(new TextEncoder().encode(text).length>maxBytes)throw new Error('response_too_large');
        return {status:r.status,url:r.url,headers:Object.fromEntries(r.headers),text};
      } finally {clearTimeout(timer);}
    }""",
        {"url": url, "budget": budget_ms, "maxBytes": max_bytes},
    )


def valid_market(html, url, site, ticker):
    from doxagent.message_bus_v2.market_sources import parse_investorshub
    from .investing_document import listing_ticker

    if inspect_html(html, url, "", generic_extraction=False).access_reason:
        return False
    try:
        if site == "investing":
            return listing_ticker(html, url) == ticker
        if site == "investorshub":
            return urlsplit(url).path.rstrip("/").split("/")[-2].rsplit("-", 1)[
                -1
            ].upper() == ticker and bool(parse_investorshub(html, ticker=ticker, now=datetime.now(UTC)))
    except (ValueError, RuntimeError, IndexError):
        return False  # A changed/incomplete contract must take normal navigation recovery.
    return False


async def yahoo_rows(page, html, ticker, parameters):
    from lxml import html as H
    import json
    from doxagent.message_bus_v2.yahoo_sources import capture_timestamp_rows, hydration_news_rows

    doc = H.fromstring(html)
    scripts = [
        {"url": s.get("data-url", ""), "text": s.text or ""}
        for s in doc.xpath("//script[@data-sveltekit-fetched]")
    ]
    rows = hydration_news_rows(scripts)
    if rows is not None:
        return rows, {"capture_method": "origin_fetch_hydration", "network_status": 200}
    cards = []
    for card in doc.xpath('//*[@data-testid="storyitem"]'):
        for link in card.xpath(".//a[@href][.//h2 or .//h3]"):
            tag = json.loads(link.get("data-yga") or "{}")
            if tag.get("yModuleName") != "qsp-news":
                continue
            from urllib.parse import urljoin

            url = urljoin("https://finance.yahoo.com", link.get("href"))
            if urlsplit(url).hostname != "finance.yahoo.com" or not urlsplit(url).path.endswith(
                ".html"
            ):
                continue
            cards.append(
                {
                    "id": tag.get("yDestinationContentUUID") or url,
                    "title": " ".join(link.text_content().split()),
                    "link": url,
                    "publisher": tag.get("yDestinationContentPartner")
                    or tag.get("yContentPartner")
                    or "Yahoo Finance",
                }
            )
    if not cards:
        raise ValueError("yahoo_origin_listing_contract_changed")
    cards = list({r["link"]: r for r in [*parameters.get("pending_rows", []), *cards]}.values())
    return await capture_timestamp_rows(
        page,
        ticker,
        cards,
        known_dates=parameters.get("known_dates", {}),
        probe_offset=parameters.get("probe_offset", 0),
        max_probes=int(parameters.get("max_probes", 0)),
    )
