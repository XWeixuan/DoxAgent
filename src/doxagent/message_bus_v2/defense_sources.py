"""Defense/space endpoints: shared public HTTP, isolated checkpoints, no Chrome identity."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import urlencode, urljoin, urlsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from lxml import html

from .industry_sources import _feed_time, _message, parse_shared_feed
from .schema import (
    AcquisitionFailure,
    AcquisitionMode,
    DistributionPolicy,
    PollContext,
    PollingConfig,
    PollResult,
    SchedulerConstraints,
    SearchPolicy,
    SharedPollContext,
    SourceDefinition,
    SourceKind,
    sha256_text,
)


@dataclass(frozen=True)
class Endpoint:
    source_id: str
    publisher: str
    urls: tuple[str, ...]
    tickers: tuple[str, ...]
    parser: str = "rss"
    mode: AcquisitionMode = AcquisitionMode.BY_DISTRIBUTION
    route: str = "direct"
    language: str = "en"
    body_route: str | None = None

    @property
    def host(self):
        return urlsplit(self.urls[0]).hostname


CATALOG = (
    Endpoint(
        "aviationweek_rocketlab",
        "Aviation Week / Rocket Lab",
        ("https://aviationweek.com/term/rocket-lab",),
        ("RKLB",),
        "aviation",
        AcquisitionMode.BY_TICKER,
    ),
    Endpoint(
        "aviationweek_rss",
        "Aviation Week",
        ("https://aviationweek.com/awn/rss-feed-by-content-source",),
        ("RKLB",),
    ),
    Endpoint(
        "breakingdefense_space",
        "Breaking Defense / Space",
        ("https://breakingdefense.com/category/space/feed/",),
        ("RKLB",),
        route="de-standard-1",
    ),
    Endpoint(
        "defensescoop_space",
        "DefenseScoop / Space",
        ("https://defensescoop.com/news/space/feed/",),
        ("RKLB",),
    ),
    Endpoint(
        "airspaceforces_space",
        "Air & Space Forces / Space",
        ("https://www.airandspaceforces.com/category/space/feed/",),
        ("RKLB",),
    ),
    Endpoint(
        "spacecom_launches",
        "Space.com / Launches & Spacecraft",
        ("https://www.space.com/feeds/tag/launches-spacecraft",),
        ("RKLB",),
        route="us-standard-5",
    ),
    Endpoint("spacenews_rss", "SpaceNews", ("https://spacenews.com/feed/",), ("RKLB",)),
    Endpoint(
        "ssc_newsroom",
        "Space Systems Command",
        ("https://www.ssc.spaceforce.mil/Newsroom",),
        ("RKLB",),
        "ssc",
        AcquisitionMode.BY_TICKER,
        "us-standard-5",
    ),
    Endpoint(
        "nasa_2026_releases",
        "NASA / 2026 news releases",
        (
            "https://www.nasa.gov/wp-json/wp/v2/press-release?per_page=50&after=2026-01-01T00%3A00%3A00&before=2027-01-01T00%3A00%3A00&_fields=id,link,date_gmt,title,excerpt,content,modified_gmt",
        ),
        ("RKLB",),
        "nasa",
    ),
    # Contracts are daily omnibus documents. Match subscribed tickers only after BODY,
    # rather than broadcasting every daily award or filtering its generic RSS title.
    Endpoint(
        "dod_contracts",
        "DoD / Contracts",
        ("https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=50",),
        ("RKLB", "ONDS"),
    ),
    Endpoint(
        "dod_releases",
        "DoD / News Releases",
        ("https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=50",),
        ("RKLB", "ONDS"),
        route="de-standard-1",
    ),
    Endpoint(
        "iridium_press_releases",
        "Iridium / Press Releases",
        ("https://investor.iridium.com/press-releases",),
        ("RKLB",),
        "iridium",
        AcquisitionMode.BY_TICKER,
    ),
    Endpoint(
        "calcalist_search",
        "Calcalist / Search",
        ("https://www.calcalist.co.il/category/3735",),
        ("ONDS",),
        "calcalist",
        AcquisitionMode.BY_SEARCH,
        "us-standard-5",
        "he",
        "de-standard-1",
    ),
    Endpoint(
        "defensescoop_drones",
        "DefenseScoop / Drones & Counter-drone",
        (
            "https://defensescoop.com/tag/drones/feed/",
            "https://defensescoop.com/tag/counter-drone/feed/",
        ),
        ("ONDS",),
    ),
    Endpoint(
        "unmannedairspace_counteruas",
        "Unmanned Airspace / Counter-UAS",
        (
            "https://www.unmannedairspace.info/category/counter-uas-systems-and-policies/feed/",
            "https://www.unmannedairspace.info/category/counter-uas-systems-tenders/feed/",
        ),
        ("ONDS",),
    ),
    Endpoint(
        "shephard_uncrewed",
        "Shephard / Uncrewed Vehicles",
        ("https://www.shephardmedia.com/news/uv-online/feed/",),
        ("ONDS",),
    ),
    Endpoint(
        "army_news_releases",
        "U.S. Army / Press Releases",
        ("https://www.army.mil/news/newsreleases",),
        ("ONDS",),
        "army",
        route="us-standard-5",
    ),
    Endpoint(
        "army_news_rss",
        "U.S. Army / News",
        ("https://www.army.mil/rss/static/0.xml",),
        ("ONDS",),
        route="us-standard-5",
    ),
)
ENDPOINTS = {item.source_id: item for item in CATALOG}


def initial_sources():
    return [
        SourceDefinition(
            source_id=e.source_id,
            display_name=e.publisher,
            adapter_ref="builtin:defense_public@1",
            kind=SourceKind.API if e.parser in {"rss", "nasa"} else SourceKind.CRAWLER,
            acquisition_mode=e.mode,
            entry_url=e.urls[0],
            content_language=e.language,
            scheduler_group="defense." + e.host.removeprefix("www."),
            scheduler_constraints=SchedulerConstraints(
                max_concurrency=2, minimum_request_gap_seconds=0.5
            ),
            default_polling_config=PollingConfig(target_interval_seconds=60, active_windows=[]),
            distribution_policy=DistributionPolicy(jev_enabled=True, require_complete_body=True)
            if e.mode == AcquisitionMode.BY_DISTRIBUTION
            else None,
            search_policy=SearchPolicy(mode="separate")
            if e.mode == AcquisitionMode.BY_SEARCH
            else None,
            parameter_schema={"type": "object", "additionalProperties": False, "properties": {}},
        )
        for e in CATALOG
    ]


@dataclass
class Capture:
    status: int
    url: str
    text: str
    headers: dict


class DefenseHttp:
    """One pooled lightweight client; no environment proxy, browser, or resident pages."""

    def __init__(self, session=None):
        self.session = session
        self.global_slots = asyncio.Semaphore(4)
        self.host_slots = {}
        self.host_locks = {}
        self.started = {}

    async def fetch(self, url, endpoint, *, headers=None):
        from .http_routes import HTTP_EGRESSES

        allowed = {endpoint.host}
        if endpoint.parser == "calcalist":
            allowed.add("cse.google.com")
        for _ in range(4):
            host = urlsplit(url).hostname
            if urlsplit(url).scheme != "https" or host not in allowed:
                raise ValueError("defense_redirect_host_not_approved")
            if self.session is None:
                from curl_cffi.requests import AsyncSession

                self.session = AsyncSession(impersonate="chrome", trust_env=False)
            slots = self.host_slots.setdefault(host, asyncio.Semaphore(2))
            lock = self.host_locks.setdefault(host, asyncio.Lock())
            async with asyncio.timeout(12), self.global_slots, slots:
                async with lock:
                    await asyncio.sleep(max(0, self.started.get(host, 0) + 0.5 - time.monotonic()))
                    self.started[host] = time.monotonic()
                if host == "spacenews.com":
                    # Its public origin accepts standard-library TLS while the
                    # Chrome TLS fingerprint is challenged. Keep a fixed route.
                    from .corporate_ir.http import PublicHttp

                    work = asyncio.create_task(
                        asyncio.to_thread(
                            PublicHttp._stdlib_read,
                            url,
                            {"User-Agent": "Mozilla/5.0", **(headers or {})},
                        )
                    )
                    try:
                        result = await asyncio.shield(work)
                    except asyncio.CancelledError:
                        while not work.done():
                            try:
                                await asyncio.shield(work)
                            except asyncio.CancelledError:
                                continue
                            except Exception:
                                break
                        await asyncio.gather(work, return_exceptions=True)
                        raise
                    if result.status in {301, 302, 303, 307, 308}:
                        url = urljoin(url, result.headers["location"])
                        continue
                    return Capture(result.status, result.url, result.body, result.headers)
                async with self.session.stream(
                    "GET",
                    url,
                    timeout=8,
                    allow_redirects=False,
                    proxy=HTTP_EGRESSES[endpoint.route],
                    headers={
                        "Accept": "*/*",
                        "Accept-Language": "en-US,en;q=0.8,he;q=0.7",
                        **(headers or {}),
                    },
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers["location"])
                        continue
                    data = bytearray()
                    async for chunk in response.aiter_content():
                        data.extend(chunk)
                        if len(data) > 4 * 1024 * 1024:
                            raise ValueError("defense_response_exceeds_4mib")
                    return Capture(
                        response.status_code,
                        url,
                        bytes(data).decode(response.encoding or "utf-8", errors="replace"),
                        {k.lower(): v for k, v in response.headers.items()},
                    )
        raise ValueError("defense_redirect_limit")

    async def close(self):
        if self.session is not None:
            await self.session.close()


def _plain(value):
    return " ".join(
        html.fromstring("<div>" + unescape(value or "") + "</div>").text_content().split()
    )


def _row(e, url, title, date, *, summary=None, raw=None, now):
    if urlsplit(url).hostname != e.host or not title:
        raise ValueError("defense_article_identity_invalid")
    stamp, basis = _feed_time(date, language=e.language, now=now)
    return _message(
        source=e.publisher,
        url=url,
        title=_plain(title),
        summary=summary,
        published_at=stamp,
        basis=basis,
        language=e.language,
        payload=raw or {},
    )


def parse_listing(e, raw, url, now):
    if e.parser == "rss":
        messages = parse_shared_feed(
            raw, feed_url=url, publisher=e.publisher, language=e.language, now=now
        )
        formats = {
            item.findtext("link"): {
                (c.text or "").strip().casefold() for c in item.findall("category")
            }
            for item in ET.fromstring(raw).findall(".//item")
        }
        for message in messages:
            if message.summary:
                message.summary = _plain(message.summary)
            if formats.get(message.url, set()) & {"video", "podcast", "podcasts"}:
                message.metadata["defense_media_format"] = "video_or_podcast"
        return messages
    if e.parser == "nasa":
        rows = json.loads(raw)
        if not isinstance(rows, list) or not rows:
            raise ValueError("nasa_release_contract_empty")
        return [
            _row(
                e,
                r["link"],
                r["title"]["rendered"],
                r["date_gmt"] + "+00:00",
                summary=_plain(r.get("excerpt", {}).get("rendered")),
                raw={
                    "article_html": r.get("content", {}).get("rendered"),
                    "article_url": r["link"],
                    "modified": r.get("modified_gmt"),
                },
                now=now,
            )
            for r in rows
            if r["date_gmt"].startswith("2026-")
        ]
    doc = html.fromstring(raw)
    rows = []
    if e.parser == "aviation":
        cards = doc.xpath(
            '//div[contains(concat(" ",normalize-space(@class)," ")," teaser__content ")]'
        )
        for card in cards:
            links = card.xpath(".//a[@href]")
            dates = card.xpath(".//time/@datetime")
            if links and dates:
                rows.append(
                    _row(
                        e,
                        urljoin(url, links[0].get("href")),
                        links[0].text_content(),
                        dates[0],
                        now=now,
                    )
                )
    elif e.parser == "iridium":
        for card in doc.xpath(
            '//div[contains(concat(" ",normalize-space(@class)," ")," wd_item_wrapper ")]'
        ):
            links = card.xpath('.//div[@class="wd_title"]/a')
            dates = card.xpath('.//div[@class="wd_date"]/text()')
            if links and dates:
                date = datetime.strptime(dates[0].strip(), "%b %d, %Y").replace(
                    tzinfo=ZoneInfo("America/New_York")
                )
                m = _row(
                    e,
                    urljoin(url, links[0].get("href")),
                    links[0].text_content(),
                    date.date().isoformat(),
                    now=now,
                )
                m.published_at, m.publication_time_basis = date, "DATE"
                rows.append(m)
    elif e.parser == "ssc":
        for card in doc.xpath('//article[contains(@class,"article-listing-item")]'):
            link = card.xpath(".//h1/a")[0]
            date = card.xpath(".//time/@datetime")[0]
            m = _row(e, urljoin(url, link.get("href")), link.text_content(), date, now=now)
            m.published_at = m.published_at.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
            rows.append(m)
    elif e.parser == "army":
        for link in doc.xpath('//li/a[contains(@href,"/article/")]'):
            dates = link.getparent().xpath('.//span[@class="date"]/text()')
            if dates:
                date = datetime.strptime(
                    dates[0].replace(".", "").replace("Sept ", "Sep ").strip(), "%b %d, %Y"
                ).replace(tzinfo=ZoneInfo("America/New_York"))
                m = _row(
                    e,
                    urljoin(url, link.get("href")),
                    link.text_content(),
                    date.date().isoformat(),
                    now=now,
                )
                m.published_at, m.publication_time_basis = date, "DATE"
                rows.append(m)
    if not rows:
        raise ValueError("defense_listing_empty_or_changed:" + e.parser)
    return rows


def parse_search(raw, e, now):
    # Decode the fixed JSONP envelope; never evaluate publisher JavaScript.
    value = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    if "error" in value or "results" not in value and "cursor" not in value:
        raise ValueError("calcalist_search_contract_changed")
    rows = []
    for r in value.get("results", []):
        url = r.get("unescapedUrl") or r.get("url") or ""
        if urlsplit(url).hostname != e.host or "/article/" not in urlsplit(url).path:
            continue
        meta = r.get("richSnippet", {}).get("metatags", {})
        rows.append(
            _row(
                e,
                url,
                r.get("titleNoFormatting") or r.get("title"),
                meta.get("articlePublishedTime") or meta.get("vrPublishedTime"),
                summary=r.get("contentNoFormatting"),
                now=now,
            )
        )
    return rows, value.get("cursor", {})


class DefenseAdapter:
    def __init__(self, http=None):
        self.http = http or DefenseHttp()
        self.cse = None
        self.cse_lock = asyncio.Lock()

    async def close(self):
        await self.http.close()

    async def _search(self, e, query, start=0):
        async with self.cse_lock:
            if self.cse is None or self.cse[0] < time.monotonic():
                cx = "partner-pub-0047680307646695:9309925491"
                r = await self.http.fetch(
                    "https://cse.google.com/cse.js?" + urlencode({"cx": cx}), e
                )
                if r.status != 200:
                    raise RuntimeError(f"calcalist_cse_bootstrap_http_{r.status}")
                fields = {}
                for name in ("cse_token", "cselibVersion"):
                    found = re.search(r'"' + name + r'"\s*:\s*"([^"\\]+)"', r.text)
                    if not found:
                        raise ValueError("calcalist_cse_bootstrap_contract_changed")
                    fields[name] = found[1]
                self.cse = (time.monotonic() + 900, fields)
        fields = self.cse[1]
        params = {
            "rsz": "filtered_cse",
            "num": "10",
            "hl": "iw",
            "source": "gcsc",
            "cselibv": fields["cselibVersion"],
            "cx": "partner-pub-0047680307646695:9309925491",
            "q": query,
            "safe": "active",
            "cse_tok": fields["cse_token"],
            "gl": "il",
            "filter": "0",
            "exp": "cc,esbfa",
            "callback": "callback",
            "sort": "date",
            "start": start,
            "rurl": e.urls[0] + "#gsc.tab=0&gsc.q=" + query,
        }
        capture = await self.http.fetch(
            "https://cse.google.com/cse/element/v1?" + urlencode(params), e
        )
        if capture.status != 200:
            self.cse = None
            raise RuntimeError(f"calcalist_search_http_{capture.status}")
        return capture

    async def poll(self, context: PollContext):
        return await self._poll(context)

    async def poll_shared(self, context: SharedPollContext):
        return await self._poll(context)

    async def _poll(self, context):
        e = ENDPOINTS[context.source.source_id]
        checkpoint = dict(context.checkpoint)
        states = dict(checkpoint.get("endpoints", {}))
        messages, failures, evidence = {}, [], []
        covered_endpoints = []
        requests = list(e.urls)
        if e.parser == "calcalist":
            queries = (context.query_plan or {}).get("queries", [])
            if not queries:
                raise ValueError("calcalist_requires_unified_L1_query_plan")
            requests = [q["query"] for q in queries]

        async def one(key):
            state = dict(states.get(key, {}))
            try:
                async with context.request_permit():
                    if e.parser == "calcalist":
                        capture = await self._search(e, key, int(state.get("page", 0)))
                    else:
                        headers = {}
                        if state.get("baseline"):
                            if state.get("etag"):
                                headers["If-None-Match"] = state["etag"]
                            if state.get("last_modified"):
                                headers["If-Modified-Since"] = state["last_modified"]
                        capture = await self.http.fetch(key, e, headers=headers)
                if capture.status == 304:
                    if not state.get("baseline"):
                        raise ValueError("defense_304_without_baseline")
                    rows = []
                elif capture.status != 200:
                    raise RuntimeError(f"defense_listing_http_{capture.status}")
                else:
                    rows = (
                        parse_search(capture.text, e, context.requested_at)[0]
                        if e.parser == "calcalist"
                        else await asyncio.to_thread(
                            parse_listing, e, capture.text, capture.url, context.requested_at
                        )
                    )
                    # Only parsed responses may advance the validator baseline.
                    state.update(
                        baseline=True,
                        etag=capture.headers.get("etag"),
                        last_modified=capture.headers.get("last-modified"),
                    )
                first_seen = dict(state.get("first_seen", {}))
                for m in rows:
                    if m.publication_time_basis == "UNKNOWN_FIRST_SEEN":
                        old = first_seen.setdefault(m.url, context.requested_at.isoformat())
                        m.published_at = datetime.fromisoformat(old)
                    m.metadata.update(
                        provider="defense_public",
                        defense_source_id=e.source_id,
                        defense_http_route=e.route,
                        content_language=e.language,
                    )
                    messages[m.url] = m
                if e.parser == "calcalist":
                    # Refresh the head every other visit while continuing deeper search pages.
                    cursor = parse_search(capture.text, e, context.requested_at)[1]
                    page = int(state.get("page", 0))
                    pages = cursor.get("pages", [])
                    state["page"] = 0 if page or len(pages) <= 1 else int(state.get("tail", 10))
                    if page:
                        state["tail"] = page + 10 if len(pages) > page // 10 + 1 else 10
                state.update(
                    first_seen=dict(list(first_seen.items())[-500:]),
                    last_success_at=context.requested_at.isoformat(),
                    error=None,
                )
                if rows:
                    known_dates = [
                        m.published_at
                        for m in rows
                        if m.publication_time_basis != "UNKNOWN_FIRST_SEEN"
                    ]
                    state["oldest"] = min(known_dates).isoformat() if known_dates else None
                lower = context.window_start or context.requested_at - timedelta(minutes=30)
                covered_endpoints.append(
                    bool(state.get("oldest")) and datetime.fromisoformat(state["oldest"]) <= lower
                )
                states[key] = state
                evidence.append(
                    {
                        "endpoint": key,
                        "http_status": capture.status,
                        "items": len(rows),
                        "route": e.route,
                    }
                )
            except Exception as exc:
                state["error"] = type(exc).__name__ + ":" + str(exc)[:180]
                states[key] = state
                failures.append(
                    AcquisitionFailure(
                        source_id=e.source_id,
                        binding_id=getattr(
                            getattr(context, "binding", None), "binding_id", "shared:" + e.source_id
                        ),
                        ticker=getattr(context, "ticker", "*"),
                        error_code="DEFENSE_ENDPOINT_FAILED",
                        error_message=state["error"],
                        raw_hash=sha256_text(key),
                        original_payload={"endpoint": key},
                    )
                )

        await asyncio.gather(*(one(key) for key in requests))
        if len(failures) == len(requests):
            raise RuntimeError(
                "defense_all_endpoints_failed:" + ";".join(f.error_message for f in failures)
            )
        checkpoint["endpoints"] = states
        covered = len(covered_endpoints) == len(requests) and all(covered_endpoints)
        return PollResult(
            messages=list(messages.values()),
            failures=failures,
            next_checkpoint=checkpoint,
            window_coverage="COMPLETE" if covered and not failures else "PARTIAL",
            window_done=True,
            acquisition_metadata={
                "endpoints": evidence,
                "partial_endpoint_failures": len(failures),
            },
        )
