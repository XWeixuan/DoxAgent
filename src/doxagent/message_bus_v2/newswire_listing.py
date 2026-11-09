"""Low-cost wire discovery, on the existing Collector and BODY pipeline."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from html import unescape
from types import SimpleNamespace
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from lxml import html

from .schema import AcquisitionFailure, PollResult, RawMessageInput, sha256_text

# These are discovery recipes, not new sources or browser identities. RSS URLs
# remain in source.endpoints and are used only after a primary failure.
RECIPES = {
    "prnewswire_massive": {
        "url": "https://www.prnewswire.com/news-releases/news-releases-list/?page={page}&pagesize=100",
        "method": "POST", "routes": ["direct"], "size": 100,
    },
    "globenewswire_massive": {
        "url": "https://www.globenewswire.com/en/Search/load/more?page={page}&pageSize=50",
        "method": "GET", "routes": ["direct"], "size": 50,
    },
    "access_newswire_massive": {
        "url": "https://www.accessnewswire.com/newsroom?page={page}",
        "method": "GET", "routes": ["us-standard-5"], "size": 20,
    },
    "businesswire_massive": {
        "url": "https://www.businesswire.com/newsroom?page={page}",
        "method": "POST", "routes": ["direct"], "size": 10, "max_pages": 8,
        "headers": {"RSC": "1"},
    },
    "tmx_newsfile_massive": {
        "url": "https://www.newsfilecorp.com/search",
        "review": "AWS WAF JavaScript challenge (202) on search/news GET and POST, direct and US egress; browser review required",
    },
}
RSS_ONLY_SOURCES = {"tmx_newsfile_massive"}


def enabled(source):
    return source.source_id in RECIPES and source.default_parameters.get("discovery_mode") == "http_listing"


def canonical_article_url(url):
    # RSS and HTML encode the same Hebrew/non-ASCII slug with different hex
    # case. Percent escapes are case-insensitive; article slug text is not.
    if urlsplit(url).hostname in {"www.globenewswire.com", "globenewswire.com"}:
        return re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(), url)
    parts = urlsplit(url)
    if parts.hostname in {"www.businesswire.com", "businesswire.com"} and re.match(r"^/news/home/\d+/[^/]+", parts.path):
        # Keep the old RSS article locator, including its terminal slash.
        return urlunsplit(("https", "www.businesswire.com", parts.path.rstrip("/")+"/", "", ""))
    return url


def businesswire_item_key(url):
    parts = urlsplit(url)
    match = re.match(r"^/news/home/(\d+)/([a-zA-Z-]+)(?:/|$)", parts.path)
    if parts.hostname in {"www.businesswire.com", "businesswire.com"} and match:
        return "businesswire:"+match[1]+":"+match[2].lower()
    return None


def parse_businesswire(text, source):
    data = None
    for match in re.finditer(r'"news":\s*(?=\{)', text):
        try:
            candidate, _ = json.JSONDecoder().raw_decode(text[match.end():])
        except ValueError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("items"), list):
            data = candidate
            break
    if not data or not data["items"] or not isinstance(data.get("currentPage"), int):
        raise ValueError("businesswire_rsc_news_missing_or_challenge")
    rows = []
    for item in data["items"]:
        identifier, language, headline = item.get("newsItemId", ""), item.get("language", ""), item.get("headline", "")
        if not re.fullmatch(r"\d+", identifier) or not re.fullmatch(r"[a-zA-Z-]+", language) or not headline:
            raise ValueError("businesswire_invalid_article_identity")
        title = unescape(headline)
        # Match the publisher's client link builder, not an invented URL slug.
        slug = re.sub(r"[^a-zA-Z0-9\-\s.€$£¥_]", "", headline.strip()) if language == "en" else ""
        slug = re.sub(r"\s+", "-", slug)[:1000]
        url = canonical_article_url("https://www.businesswire.com/news/home/"+identifier+"/"+language+("/"+quote(slug, safe="-_.!~*'()") if slug else ""))
        published = datetime.fromisoformat(item["releaseDate"].replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError("businesswire_publication_timezone_missing")
        rows.append(RawMessageInput(title=title, summary=None, body=None, url=url,
            source=source.display_name, source_item_key=url, published_at=published,
            raw_payload={"newsItemId": identifier, "listing_summary": item.get("teaser"), "releaseDate": item["releaseDate"]},
            metadata={"language": {"zh-HK":"zh-Hant", "zh-CN":"zh-Hans"}.get(language, language),
                      "publication_time_precision":"second", "listing_page":data["currentPage"],
                      "identity_evidence":{"id_kind":"canonical_url", "url_kind":"article"}}))
    return rows


def parse_listing(text, source, now):
    from .newswire_sources import parse_access

    if source.source_id == "access_newswire_massive":
        return parse_access(text, now)
    if source.source_id == "businesswire_massive":
        return parse_businesswire(text, source)
    root = html.fromstring(text)
    rows = []
    eastern = ZoneInfo("America/New_York")
    prn = source.source_id == "prnewswire_massive"
    cards = root.xpath('//a[contains(concat(" ",normalize-space(@class)," ")," newsreleaseconsolidatelink ")]') if prn else root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," newsLink ")]')
    day = root.xpath('//*[@id="date"]/@value') if prn else []
    for card in cards:
        if prn:
            dates = card.xpath('.//h3/small')
            headings = card.xpath('.//h3')
            if not dates or not headings:
                raise ValueError("prn_card_missing_title_or_time")
            stamp = dates[0].text_content().strip().removesuffix(" ET")
            # The page supplies its Eastern calendar date. Never infer yesterday
            # from the local clock, or label a minute timestamp as second-exact.
            if len(stamp) == 5 and day:
                published = datetime.strptime(day[0] + " " + stamp, "%m/%d/%Y %H:%M")
            else:
                published = datetime.strptime(stamp, "%b %d, %Y, %H:%M")
            heading = copy.deepcopy(headings[0])
            for node in heading.xpath(".//small"):
                node.drop_tree()
            title = " ".join(heading.text_content().split())
            link = card.get("href", "")
            summaries = card.xpath(".//p")
            language = "en"
        else:
            anchors = card.xpath('.//div[contains(@class,"mainLink")]/a')
            dates = card.xpath('.//div[contains(@class,"date-source")]/span[1]')
            if not anchors or not dates:
                raise ValueError("gn_card_missing_title_or_time")
            stamp = dates[0].text_content().strip()
            published = datetime.strptime(stamp, "%B %d, %Y %H:%M ET")
            title = " ".join(anchors[0].text_content().split())
            link = anchors[0].get("href", "")
            summaries = card.xpath('.//div[contains(@class,"newsTxt")]')
            parts = urlsplit(link).path.split("/")
            language = parts[7] if len(parts) > 7 else "en"
        url = canonical_article_url(urljoin(source.entry_url, link))
        if urlsplit(url).hostname != urlsplit(source.entry_url).hostname or not title:
            raise ValueError("wire_invalid_article_link_or_title")
        if not (urlsplit(url).path.endswith(".html") and "/news-release" in url):
            raise ValueError("wire_non_article_link")
        summary = " ".join(summaries[0].text_content().split()) if summaries else None
        rows.append(RawMessageInput(
            title=title, summary=summary if prn else None,
            body=None, url=url, source=source.display_name, source_item_key=url,
            published_at=published.replace(tzinfo=eastern).astimezone(UTC),
            raw_payload={"listing_time": stamp, "listing_summary": summary},
            metadata={"language": language, "publication_time_precision": "minute",
                      "identity_evidence": {"id_kind": "canonical_url", "url_kind": "article"}},
        ))
    if not rows:
        raise ValueError("wire_listing_empty_or_challenge")
    return rows


def cache_evidence(response, url, method):
    from .public_listing import cache_evidence as check_cache

    evidence = {key: response.headers.get(key) for key in
                ("age", "cache-control", "etag", "last-modified", "date", "warning", "x-cache")}
    evidence.update(url=url, method=method, status=response.status_code)
    try:
        evidence["validation"] = check_cache(SimpleNamespace(headers=response.headers), SimpleNamespace(max_age_seconds=120))
    except ValueError as exc:
        evidence["error"] = str(exc)
    return evidence


async def poll_listing(adapter, context):
    from .collection_override import CollectionStopped
    from .poll_trace import REQUESTS, REQUEST_ROLE, validate_listing

    source = context.source
    recipe = RECIPES[source.source_id]
    previous = context.checkpoint or {}
    head = previous.get("head", {})
    old = previous.get("seen", {})
    events = REQUESTS.get()
    offset = len(events or [])
    caches, rows, rss_results = [], {}, []
    started = time.monotonic()
    cutoff = context.requested_at - timedelta(minutes=30)
    try:
        if recipe.get("review"):
            # Already tested in both networks. Do not hammer a known challenge
            # every 30 seconds until the user chooses a browser/authorized path.
            raise ValueError(recipe["review"])
        async with asyncio.timeout(12):
            for page in range(1, recipe.get("max_pages", 4)+1):
                url = recipe["url"].format(page=page)
                headers = dict(recipe.get("headers", {}))
                if page == 1 and recipe["method"] == "GET" and head.get("baseline") and not context.force_refresh:
                    headers = {name: head[key] for name, key in
                               [("If-None-Match", "etag"), ("If-Modified-Since", "modified")] if head.get(key)}
                response, route = await adapter.listing_request(context, url, recipe, headers)
                caches.append(cache_evidence(response, url, recipe["method"]))
                if caches[-1].get("error"):
                    raise ValueError(caches[-1]["error"])
                if response.status_code == 304:
                    if page != 1 or not head.get("baseline"):
                        raise ValueError("wire_listing_304_without_baseline")
                    break
                if response.status_code != 200:
                    raise ValueError("wire_listing_http_" + str(response.status_code))
                parsed = await asyncio.to_thread(parse_listing, response.text, source, context.requested_at)
                if any(row.metadata.get("listing_page", page) != page for row in parsed):
                    raise ValueError("wire_listing_requested_page_ignored")
                if page == 1:
                    head = dict(baseline=True, etag=response.headers.get("etag"), modified=response.headers.get("last-modified"))
                for row in parsed:
                    row.metadata["discovery_method"] = "http_listing"
                    row.raw_payload["listing_url"] = url
                    rows.setdefault(row.url, row)
                # Visit every card, including equal timestamps. Follow pages on
                # bursts/recovery until a known article or the admission boundary.
                if set(old).intersection(r.url for r in parsed) or max(r.published_at for r in parsed) <= cutoff or len(parsed) < recipe["size"]:
                    break
            else:
                raise ValueError("wire_listing_window_overflow:no_overlap_within_page_budget")
        seen = dict(old)
        delta = []
        for row in rows.values():
            hint = hashlib.sha256((str(row.title) + str(row.summary)).encode()).hexdigest()
            if row.published_at >= cutoff and (context.force_refresh or old.get(row.url) != hint):
                delta.append(row)
            seen[row.url] = hint
        ids = [e["physical_request_id"] for e in (events or [])[offset:]
               if e.get("role", "listing") == "listing" and e.get("status_code") in (200, 304)]
        proven = validate_listing(events or [], ids, since=offset)
        return PollResult(messages=delta, next_checkpoint={"head": head, "seen": dict(list(seen.items())[-1024:])},
                          window_done=True, window_coverage="PARTIAL", acquisition_metadata={
                              "method": "http_listing", "request_seconds": round(time.monotonic()-started, 3),
                              "pages": len(caches), "feed_items": len(rows), "delta_items": len(delta),
                              "route": route, "cache": caches, "validated_request_ids": [e["physical_request_id"] for e in proven]})
    except CollectionStopped:
        raise
    except Exception as exc:
        endpoints = [e for e in source.default_parameters["endpoints"] if e["format"] == "rss"]
        failure = AcquisitionFailure(source_id=source.source_id, binding_id="shared:"+source.source_id, ticker="*",
            error_code="HTTP_LISTING_FAILED_RSS_FALLBACK" if endpoints else "HTTP_LISTING_FAILED",
            error_message=type(exc).__name__+":"+str(exc)[:280], raw_hash=sha256_text(recipe["url"]),
            original_payload={"listing_url": recipe["url"], "cache": caches})
        token = REQUEST_ROLE.set("rss_fallback")
        try:
            async with asyncio.timeout(12):
                for endpoint in endpoints:
                    fallback = context.model_copy(update={"acquisition_unit": "WIRE:"+endpoint["endpoint_id"], "checkpoint": {}})
                    try:
                        rss_results.extend((await adapter.poll_feed(fallback)).messages)
                    except CollectionStopped:
                        raise
                    except Exception as error:
                        failure.error_message += ";rss:"+type(error).__name__+":"+str(error)[:80]
        except CollectionStopped:
            raise
        except TimeoutError:
            failure.error_message += ";rss:total_timeout"
        finally:
            REQUEST_ROLE.reset(token)
        for row in rss_results:
            rows.setdefault(row.url, row)
        for row in rows.values():
            row.metadata.update(discovery_method="rss_fallback" if row in rss_results else "http_listing_partial", listing_error=failure.error_message)
        return PollResult(messages=list(rows.values()), failures=[failure], window_done=False,
                          acquisition_metadata={"method": "rss_fallback" if endpoints else "http_listing_failed",
                              "cache": caches, "rss_items": len(rss_results), "error": failure.error_message,
                              "validated_request_ids": []})
