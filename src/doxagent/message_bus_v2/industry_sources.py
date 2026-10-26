"""Trusted list and feed adapters for semiconductor/AI-hardware publishers."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from lxml import html as lxml_html  # type: ignore[import-untyped]

from doxagent.site_strategy.client import SiteAccessError

from .http_routes import HTTP_EGRESSES, approved_routes, transport_routes
from .schema import PollContext, PollResult, RawMessageInput, SharedPollContext

_TAIPEI = ZoneInfo("Asia/Taipei")
_SEOUL = ZoneInfo("Asia/Seoul")
_NEW_YORK = ZoneInfo("America/New_York")
# Fixed deployment listeners, never arbitrary caller-provided proxy destinations.
FEED_HTTP_EGRESSES = HTTP_EGRESSES
_BARRONS_ET_DATE = re.compile(
    r"\b([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2}) ([ap])\.m\. ET"
)
_DJ_PUBLISHERS = {
    "barrons.com": "Barron's",
    "wsj.com": "The Wall Street Journal",
    "marketwatch.com": "MarketWatch",
    "fn.com": "Financial News",
    "mansionglobal.com": "Mansion Global",
    "dowjones.com": "Dow Jones",
    "financialnews.com": "Financial News",
}


def _text(node: Any) -> str:
    return " ".join(unescape(node.text_content()).split()) if node is not None else ""


def _canonical(url: str) -> str:
    parsed = urlparse(url)
    # DIGITIMES Taiwan and The Elec identify distinct articles through query IDs.
    query = (
        parsed.query if parsed.path.lower().endswith(("/shwnws.asp", "/articleview.html")) else ""
    )
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", query, ""))


def _date_only(value: str, zone: ZoneInfo) -> datetime:
    return datetime.fromisoformat(value).replace(hour=12, tzinfo=zone).astimezone(UTC)


def _message(
    *,
    source: str,
    url: str,
    title: str,
    summary: str | None,
    published_at: datetime,
    basis: str,
    language: str,
    payload: dict[str, Any],
    external_id: str | None = None,
) -> RawMessageInput:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return RawMessageInput(
        external_id=external_id or _canonical(url),
        source_item_key=external_id or _canonical(url),
        title=title,
        summary=summary or None,
        body=None,
        source=source,
        publisher_name=source,
        url=url,
        published_at=published_at,
        publication_time_basis=basis,
        raw_payload=payload,
        metadata={
            "publisher_domain": host,
            "content_language": language,
            "identity_evidence": {
                "id_kind": "stable_article" if external_id else "canonical_url",
                "url_kind": "article",
                "mutable": True,
            },
        },
    )


def _listing_guard(root: Any, body: str) -> None:
    title = _text(root.find(".//title")).lower()
    beginning = body[:10_000].lower()
    if title in {"access denied", "not found", "access is temporarily restricted"} or any(
        marker in beginning
        for marker in ("access is temporarily restricted", "unusual traffic from your")
    ):
        raise RuntimeError("publisher_listing_blocked")


def parse_trendforce_listing(html: str, *, press: bool, now: datetime) -> list[RawMessageInput]:
    root = lxml_html.fromstring(html)
    _listing_guard(root, html)
    rows: dict[str, RawMessageInput] = {}
    if press:
        links = root.xpath('//h3/a[contains(@href,"/presscenter/news/")]')
    else:
        links = root.xpath(
            '//div[contains(concat(" ",normalize-space(@class)," ")," insight-list-item ")]'
            '//a[contains(concat(" ",normalize-space(@class)," ")," title-link ")]'
            '|//div[contains(@class,"insight-carousel-list-info")]//a[@href]'
        )
    for link in links:
        url = urljoin("https://www.trendforce.com", link.get("href", ""))
        path = urlparse(url).path
        pattern = (
            r"^/presscenter/news/(\d{8})-\d+\.html$"
            if press
            else r"^/news/(\d{4})/(\d{2})/(\d{2})/[^/]+/?$"
        )
        match = re.match(pattern, path)
        if not match or urlparse(url).hostname != "www.trendforce.com":
            continue
        title = _text(link)
        if len(title) < 15:
            continue
        date = (
            f"{match[1][:4]}-{match[1][4:6]}-{match[1][6:]}"
            if press
            else f"{match[1]}-{match[2]}-{match[3]}"
        )
        try:
            published = _date_only(date, _TAIPEI)
        except ValueError:
            continue
        card = (
            link.getparent().getparent()
            if press
            else link.xpath(
                'ancestor::div[contains(concat(" ",normalize-space(@class)," "),'
                '" insight-list-item ")][1]'
            )
        )
        if isinstance(card, list):
            card = card[0] if card else None
        paragraphs = card.xpath(".//p[1]") if card is not None else []
        summary = _text(paragraphs[0]) if paragraphs else ""
        rows[_canonical(url)] = _message(
            source="TrendForce",
            url=url,
            title=title,
            summary=summary,
            published_at=published,
            basis="DATE",
            language="en",
            payload={"listing": "press" if press else "news", "date": date},
        )
    if not rows:
        raise RuntimeError("trendforce_listing_empty_or_changed")
    return list(rows.values())


def parse_digitimes_listing(html: str, *, now: datetime) -> list[RawMessageInput]:
    root = lxml_html.fromstring(html)
    _listing_guard(root, html)
    rows: dict[str, RawMessageInput] = {}
    links = root.xpath(
        '//div[contains(concat(" ",normalize-space(@class)," ")," more-news ")]'
        '//a[contains(concat(" ",normalize-space(@class)," ")," title ")][@href]'
    )
    for link in links:
        url = urljoin("https://www.digitimes.com", link.get("href", ""))
        if urlparse(url).hostname != "www.digitimes.com":
            continue
        match = re.match(r"^/news/a(\d{8})[A-Z0-9]+(?:/[^/]+)?\.html$", urlparse(url).path)
        title = _text(link)
        if not match or len(title) < 15:
            continue
        card = link.xpath(
            'ancestor::div[contains(concat(" ",normalize-space(@class)," ")," more-news ")][1]'
        )
        card = card[0] if card else None
        date_node = card.xpath('.//div[contains(@class,"date")][1]') if card is not None else []
        date_text = _text(date_node[0]) if date_node else ""
        date = f"{match[1][:4]}-{match[1][4:6]}-{match[1][6:]}"
        basis = "DATE"
        try:
            # The URL date is often the editorial creation date, not the
            # publication date shown on the listing.
            display_match = re.search(
                r"\b([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}):(\d{2})\b", date_text
            )
            if display_match:
                local_now = now.astimezone(_TAIPEI)
                local = datetime.strptime(
                    f"{local_now.year} {display_match[1]} {display_match[2]} "
                    f"{display_match[3]}:{display_match[4]}",
                    "%Y %b %d %H:%M",
                ).replace(tzinfo=_TAIPEI)
                if local > local_now + timedelta(days=7):
                    local = local.replace(year=local.year - 1)
                basis = "EXACT"
                date = local.date().isoformat()
            else:
                local = datetime.fromisoformat(date).replace(hour=12, tzinfo=_TAIPEI)
        except ValueError:
            continue
        abstract = card.xpath('.//div[contains(@class,"abstract")][1]') if card is not None else []
        rows[_canonical(url)] = _message(
            source="DIGITIMES",
            url=url,
            title=title,
            summary=_text(abstract[0]) if abstract else None,
            published_at=local.astimezone(UTC),
            basis=basis,
            language="en",
            payload={"listing": "more_news", "date": date},
        )
    if not rows:
        raise RuntimeError("digitimes_more_news_empty_or_changed")
    return list(rows.values())


def parse_barrons_ticker_listing(html: str, *, ticker: str, now: datetime) -> list[RawMessageInput]:
    root = lxml_html.fromstring(html)
    _listing_guard(root, html)
    main = root.xpath("//main")
    scope = main[0] if main else None
    if scope is None:
        cards = root.xpath('//*[@data-id="News_index"]')
        scope = cards[0] if cards else None
    # The bare stock-page shell contains a footer article, but no rendered news card.
    if scope is None:
        raise RuntimeError("barrons_ticker_news_not_rendered")
    rows: dict[str, RawMessageInput] = {}
    candidate_count = 0
    other_heading = scope.xpath(
        "//*[self::h2 or self::h3 or self::h4 or self::div or self::span]"
        '[translate(normalize-space(.),"abcdefghijklmnopqrstuvwxyz",'
        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ")="OTHER DOW JONES"]'
    )
    ordered = {node: index for index, node in enumerate(scope.iter())}
    wrapped = scope.xpath(".//section[@data-dj-section]")
    other_starts = sorted(ordered[node] for node in other_heading)
    section_ends = sorted(
        ordered[node]
        for node in scope.xpath(".//h2|.//h3|.//h4")
        if ordered[node] not in other_starts
    )
    for link in scope.xpath(".//a[@href]"):
        url = urljoin("https://www.barrons.com", link.get("href", ""))
        parsed = urlparse(url)
        host = (parsed.hostname or "").removeprefix("www.")
        if parsed.scheme != "https" or host not in _DJ_PUBLISHERS:
            continue
        if not re.search(r"/(?:articles|story|news)/[^/]+", parsed.path):
            continue
        is_barrons = host == "barrons.com"
        if wrapped:
            section = next(
                (
                    node.get("data-dj-section")
                    for node in link.iterancestors()
                    if node.get("data-dj-section")
                ),
                None,
            )
            if section not in {"barrons", "other"}:
                continue
            if is_barrons != (section == "barrons"):
                continue
        # Other publishers are admitted only under the explicitly labelled Dow Jones section.
        under_other = any(
            start < ordered[link] and not any(start < end < ordered[link] for end in section_ends)
            for start in other_starts
        )
        if not wrapped and not is_barrons and not under_other:
            continue
        title = _text(link)
        if len(title) < 15:
            continue
        candidate_count += 1
        card = link
        for _ in range(4):
            if card.getparent() is None:
                break
            card = card.getparent()
            if card.tag in {"article", "li"}:
                break
        times = card.xpath(".//time[@datetime]/@datetime")
        basis = "UNKNOWN_FIRST_SEEN"
        published = now
        if times:
            try:
                value = datetime.fromisoformat(times[0].replace("Z", "+00:00"))
                published = value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)
                basis = "EXACT"
            except ValueError:
                pass
        if basis != "EXACT":
            for ancestor in link.iterancestors():
                if ancestor is scope:
                    break
                card_links = ancestor.xpath(
                    './/a[contains(@href,"/articles/") or contains(@href,"/story/")]'
                )
                article_urls = {
                    _canonical(urljoin("https://www.barrons.com", item.get("href", "")))
                    for item in card_links
                }
                if len(article_urls) > 1:
                    break
                candidate = _text(ancestor)
                if len(candidate) > 500:
                    break
                date_match = _BARRONS_ET_DATE.search(candidate)
                if date_match:
                    month, day, year, hour, minute, meridiem = date_match.groups()
                    local = datetime.strptime(
                        f"{month} {day} {year} {hour}:{minute} {meridiem.upper()}M",
                        "%b %d %Y %I:%M %p",
                    )
                    published = local.replace(tzinfo=_NEW_YORK).astimezone(UTC)
                    basis = "EXACT"
                    break
        if basis != "EXACT":
            # A newly discovered historical card is not a newly published article.
            continue
        summary = next((_text(p) for p in card.xpath(".//p") if _text(p) != title), "")
        rows[_canonical(url)] = _message(
            source=_DJ_PUBLISHERS.get(host, host),
            url=_canonical(url),
            title=title,
            summary=summary,
            published_at=published,
            basis=basis,
            language="en",
            payload={
                "listing": "barrons_ticker",
                "ticker": ticker,
                "section": "barrons" if is_barrons else "other_dow_jones",
            },
        )
    if not rows:
        if candidate_count:
            return []
        expected_sections = set((scope.get("data-feed-channels") or "barrons,other").split(","))
        if (expected_sections and expected_sections <= {"barrons", "other"}
            and scope.get("data-barrons-feed") == "complete" and {
            node.get("data-dj-section")
            for node in wrapped
            if node.get("data-feed-complete") == "true"
        } == expected_sections):
            return []  # Both native tab contracts succeeded, with no eligible stories.
        raise RuntimeError("barrons_ticker_news_empty_or_changed")
    return list(rows.values())


class IndustryListingAdapter:
    def __init__(self, browser: Any, variant: str) -> None:
        self.browser = browser
        self.variant = variant
        self._public_session = None
        self._public_slots = asyncio.Semaphore(4)
        self._public_retry_at = 0.0

    async def close(self):
        if self._public_session is not None:
            await self._public_session.close()

    async def _barrons_http(self, context):
        from curl_cffi.requests import AsyncSession
        from .barrons_sources import render_news_feeds
        from .poll_trace import request_started
        from .schema import utc_now

        channel = {"HEAD": "Barrons", "OTHER_HEAD": "BarronsDowJonesNetwork"}.get(
            context.acquisition_unit
        )
        if channel is None:
            return None
        if self._public_session is None:
            self._public_session = AsyncSession(impersonate="chrome", trust_env=False)
        url = (
            "https://www.barrons.com/market-data/api/news?channel="
            + channel
            + "&chartingSymbol=stock%2F%2F%2F"
            + quote(context.ticker.lower(), safe="")
            + "&pageNumber=0&count=40"
        )
        async with self._public_slots, context.request_permit():
            event = request_started("GET", url, "curl_cffi")
            response = await self._public_session.get(
                url,
                proxy=HTTP_EGRESSES["us-standard-5"],
                timeout=8,
                allow_redirects=False,
                headers={"Cache-Control": "no-cache"},
            )
            received = utc_now()
            if event is not None:
                event.update(
                    response_received_at=received.isoformat(),
                    status_code=response.status_code,
                    validators={
                        k: response.headers[k]
                        for k in ("date", "age", "etag", "last-modified")
                        if k in response.headers
                    },
                )
        html = render_news_feeds(
            [{"status": response.status_code, "text": response.text}], channels=[channel]
        )
        messages = parse_barrons_ticker_listing(
            html, ticker=context.ticker, now=context.requested_at
        )
        return PollResult(
            messages=messages,
            window_coverage="PARTIAL" if context.window_start else "COMPLETE",
            acquisition_metadata={
                "listing_variant": "barrons",
                "listing_transport": "public_json",
                "channel": channel,
                "listing_received_at": received.isoformat(),
            },
        )

    async def _get(self, context: PollContext | SharedPollContext, url: str) -> PollResult:
        deadline = time.monotonic() + 45
        if self.variant == 'trendforce_press' and time.monotonic() >= self._public_retry_at:
            try:
                from curl_cffi.requests import AsyncSession
                from .poll_trace import request_started
                from .schema import utc_now
                if self._public_session is None:
                    self._public_session = AsyncSession(impersonate='chrome', trust_env=False)
                async with self._public_slots, context.request_permit():
                    event = request_started('GET', url, 'curl_cffi')
                    response = await self._public_session.get(url, proxy=HTTP_EGRESSES['server-direct'],timeout=8)
                    if event is not None:
                        event.update(response_received_at=utc_now().isoformat(),status_code=response.status_code)
                if response.status_code != 200 or urlparse(response.url).hostname != urlparse(url).hostname:
                    raise RuntimeError('trendforce_public_listing_refused')
                messages = parse_trendforce_listing(response.text,press=True,now=context.requested_at)
                return PollResult(messages=messages,window_coverage='PARTIAL' if context.window_start else 'COMPLETE',
                    acquisition_metadata={'listing_url':url,'listing_variant':self.variant,'listing_transport':'public_http'})
            except Exception:
                self._public_retry_at = time.monotonic()+60
        if self.variant == "barrons" and isinstance(context, PollContext):
            try:
                result = await self._barrons_http(context) if time.monotonic() >= self._public_retry_at else None
                if result is not None:
                    self._public_retry_at = 0.0
                    return result
            except Exception:
                # One existing identity-browser fallback; no exit rotation or retry cascade.
                self._public_retry_at = time.monotonic() + 60
        if self.browser is None:
            raise RuntimeError("publisher listing requires Site Access browser")
        try:
            run_id = context.poll_run_id if isinstance(context, PollContext) else context.run_id
            async with context.request_permit():
                recipe_options = (
                    {"recipe_ref": "builtin:barrons_ticker@1"} if self.variant == "barrons" else {}
                )
                if self.variant == "barrons" and isinstance(context, PollContext):
                    channel = {"HEAD":"Barrons", "OTHER_HEAD":"BarronsDowJonesNetwork"}.get(
                        context.acquisition_unit)
                    if channel:
                        recipe_options["recipe_parameters"] = {"channel":channel}
                status, final_url, _headers, html = await self.browser.get(
                    url,
                    operation_id=f"{run_id}:{self.variant}",
                    remaining_budget_ms=max(1, int((deadline - time.monotonic()) * 1000)),
                    **recipe_options,
                )
        except SiteAccessError as exc:
            if exc.site_access_deferred:
                return PollResult(
                    window_done=False,
                    site_access_deferred=True,
                    site_access_retry_not_before=exc.result.retry_not_before,
                    site_access_deferred_reason=exc.result.reason_code,
                )
            raise
        if status != 200 or urlparse(final_url).hostname != urlparse(url).hostname:
            raise RuntimeError(f"publisher listing HTTP {status} or redirect")
        now = context.requested_at
        if self.variant == "barrons":
            assert isinstance(context, PollContext)
            messages = parse_barrons_ticker_listing(html, ticker=context.ticker, now=now)
        elif self.variant == "digitimes":
            messages = parse_digitimes_listing(html, now=now)
        else:
            press = "/presscenter/news" in urlparse(url).path
            messages = parse_trendforce_listing(html, press=press, now=now)
        return PollResult(
            messages=messages,
            window_done=True,
            window_coverage="PARTIAL" if context.window_start else "COMPLETE",
            acquisition_metadata={
                "listing_url": url,
                "listing_variant": (
                    "trendforce_press" if "/presscenter/news" in url else self.variant
                ),
            },
        )

    async def poll(self, context: PollContext) -> PollResult:
        if self.variant != "barrons":
            raise RuntimeError("distribution listing requires poll_shared")
        ticker = quote(context.ticker.lower(), safe="")
        return await self._get(
            context,
            "https://www.barrons.com/market-data/stocks/"
            f"{ticker}?mod=searchresults_companyquotes&mod=searchbar"
            f"&search_keywords={ticker}&search_statement_type=suggested",
        )

    async def poll_shared(self, context: SharedPollContext) -> PollResult:
        if self.variant == "barrons":
            raise RuntimeError("Barron's is ticker-scoped")
        if not context.source.entry_url:
            raise RuntimeError("distribution listing entry_url is missing")
        if self.variant == "digitimes":
            return await self._get(context, context.source.entry_url)
        # TrendForce exposes five articles per page; one page is not a complete
        # real-time window during a busy semiconductor news day.
        lower = context.window_start or context.requested_at - timedelta(minutes=30)
        lower_day = lower.astimezone(_TAIPEI).date()
        max_pages = min(8, int(context.source.default_parameters.get("max_pages", 5)))
        found: dict[str, RawMessageInput] = {}
        complete = False
        pages = 0
        for page_number in range(1, max_pages + 1):
            base = context.source.entry_url
            if page_number == 1:
                url = base
            elif "/presscenter/" in base:
                url = f"{base}?page={page_number}"
            else:
                url = f"{base.rstrip('/')}/page/{page_number}/"
            result = await self._get(context, url)
            if result.site_access_deferred:
                return result
            pages += 1
            for message in result.messages:
                found[_canonical(message.url)] = message
            if len(result.messages) < 5 or any(
                message.published_at.astimezone(_TAIPEI).date() < lower_day
                for message in result.messages
            ):
                complete = True
                break
        return PollResult(
            messages=list(found.values()),
            window_done=True,
            window_coverage="COMPLETE" if complete else "PARTIAL",
            acquisition_metadata={"listing_url": context.source.entry_url, "pages": pages},
        )


def _xml_child_text(item: ElementTree.Element, names: set[str]) -> str | None:
    for child in item:
        if child.tag.rsplit("}", 1)[-1] in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def _feed_link(item: ElementTree.Element) -> str | None:
    for child in item:
        if child.tag.rsplit("}", 1)[-1] != "link":
            continue
        if child.attrib.get("rel", "alternate") == "alternate":
            return (child.attrib.get("href") or child.text or "").strip()
    return None


def _feed_time(value: str | None, *, language: str, now: datetime) -> tuple[datetime, str]:
    if not value:
        return now, "UNKNOWN_FIRST_SEEN"
    zone = _SEOUL if language == "ko" else _TAIPEI if language == "zh-Hant" else UTC
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return now, "UNKNOWN_FIRST_SEEN"
    if len(value) <= 10:
        parsed = parsed.replace(hour=12)
        basis = "DATE"
    else:
        basis = "EXACT"
    return parsed.replace(tzinfo=parsed.tzinfo or zone).astimezone(UTC), basis


def parse_shared_feed(
    xml: str, *, feed_url: str, publisher: str, language: str, now: datetime
) -> list[RawMessageInput]:
    root = ElementTree.fromstring(xml)
    items = [*root.findall(".//item"), *root.findall(".//{*}entry")]
    if not items:
        raise RuntimeError("publisher_feed_empty_or_changed")
    rows: dict[str, RawMessageInput] = {}
    feed_host = (urlparse(feed_url).hostname or "").removeprefix("rss.").removeprefix("www.")
    for item in items:
        url = _feed_link(item)
        title = _xml_child_text(item, {"title"})
        if not url or not title or len(title) < 8:
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").removeprefix("www.")
        if parsed.scheme != "https" or not (host == feed_host or host.endswith("." + feed_host)):
            continue
        published, basis = _feed_time(
            _xml_child_text(item, {"published", "pubDate", "updated"}),
            language=language,
            now=now,
        )
        summary = _xml_child_text(item, {"summary", "description"})
        summary = (
            " ".join(lxml_html.fromstring(f"<div>{summary}</div>").text_content().split())
            if summary
            else None
        )
        identifier = _xml_child_text(item, {"guid", "id"}) or _canonical(url)
        rows[_canonical(url)] = _message(
            source=publisher,
            url=_canonical(url),
            title=unescape(title),
            summary=summary,
            published_at=published,
            basis=basis,
            language=language,
            payload={"feed_url": feed_url, "guid": identifier},
            external_id=identifier,
        )
    if not rows:
        raise RuntimeError("publisher_feed_has_no_valid_articles")
    return list(rows.values())


class SharedFeedAdapter:
    def __init__(self, client: httpx.AsyncClient, publisher: str, *, browser: Any = None) -> None:
        self.client = client
        self.publisher = publisher
        self.browser = browser
        self._route_clients = {}

    async def close(self):
        for client in self._route_clients.values():
            await client.aclose()
        self._route_clients.clear()

    async def poll(self, context: PollContext) -> PollResult:
        raise RuntimeError("shared publisher feed requires poll_shared")

    async def poll_shared(self, context: SharedPollContext) -> PollResult:
        source = context.source
        if not source.entry_url or not source.content_language:
            raise RuntimeError("shared publisher feed URL/language is missing")
        proxy = source.default_parameters.get("feed_proxy_url")
        routes = source.default_parameters.get("feed_http_routes")
        if routes is not None:
            approved_routes(routes)
        browser_fallback = source.default_parameters.get("feed_browser_fallback", False)
        if browser_fallback and (
            getattr(source, "source_id", None) != "globenewswire_semiconductors_rss"
            or self.browser is None
        ):
            raise ValueError("browser feed fallback is only approved for GlobeNewswire")
        deadline = time.monotonic() + 30
        route_used = routes[0] if routes else "legacy"
        async with context.request_permit():
            # One retry for read-only connection loss; all attempts share a short
            # deadline so an RSS failure cannot occupy the shared collector for minutes.
            async with asyncio.timeout(30):
                selected_routes = approved_routes(routes) if routes else [("legacy", proxy)]
                if len(selected_routes) == 1 and not browser_fallback:
                    selected_routes *= 2

                async def send(route_proxy):
                    timeout = httpx.Timeout(12, connect=5, pool=5)
                    if route_proxy:
                        client = self._route_clients.get(str(route_proxy))
                        if client is None:
                            client = httpx.AsyncClient(proxy=str(route_proxy), timeout=timeout,
                                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))
                            from .poll_trace import install
                            install(client)
                            self._route_clients[str(route_proxy)] = client
                        return await client.get(source.entry_url)
                    return await self.client.get(source.entry_url, timeout=timeout)

                transport_errors = (
                    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                    httpx.ReadTimeout, httpx.RemoteProtocolError,
                )
                try:
                    response, route_used = await transport_routes(
                        send, selected_routes, transport_errors,
                    )
                    status = response.status_code
                    body = response.text
                except transport_errors:
                    if not browser_fallback:
                        raise
                    # The explicitly approved same-feed browser path also covers
                    # a broken HTTP connection, without stacking another budget.
                    status, body = 0, ""
                if status in (0, 403) and browser_fallback:
                    try:
                        status, _final, _headers, body = await self.browser.get(
                            source.entry_url,
                            remaining_budget_ms=max(1, int((deadline - time.monotonic()) * 1000)),
                            operation_id=f"{context.run_id}:feed-browser",
                        )
                    except SiteAccessError as exc:
                        if not exc.site_access_deferred:
                            raise
                        return PollResult(
                            window_done=False,
                            site_access_deferred=True,
                            site_access_deferred_reason=exc.result.reason_code,
                            site_access_retry_not_before=exc.result.retry_not_before,
                        )
                    if status != 200:
                        raise RuntimeError(f"publisher_browser_feed_http_{status}")
                    body = normalize_browser_feed(body)
                    route_used = "managed_browser"
        if route_used != "managed_browser":
            response.raise_for_status()
        if len(body.encode("utf-8")) > 2_000_000:
            raise RuntimeError("publisher_feed_exceeds_size_limit")
        messages = parse_shared_feed(
            body,
            feed_url=source.entry_url,
            publisher=self.publisher,
            language=source.content_language,
            now=context.requested_at,
        )
        lower = context.window_start or context.requested_at - timedelta(minutes=30)
        complete = bool(messages) and min(message.published_at for message in messages) <= lower
        return PollResult(
            messages=messages,
            window_done=True,
            window_coverage="COMPLETE" if complete else "PARTIAL",
            acquisition_metadata={
                "feed_url": source.entry_url,
                "item_count": len(messages),
                "http_route": route_used,
                "http_status": status,
                "xml_valid": True,
            },
        )


def normalize_browser_feed(body: str) -> str:
    """Chrome may display XML as escaped text; never accept an HTML refusal as XML."""
    if len(body.encode("utf-8")) > 2_000_000:
        raise RuntimeError("publisher_feed_exceeds_size_limit")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        root = None
    if root is not None and root.tag.rsplit("}", 1)[-1] in {"rss", "feed", "RDF"}:
        return body
    text = lxml_html.fromstring(body).text_content().strip()
    root = ElementTree.fromstring(text)
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed", "RDF"}:
        raise RuntimeError("publisher_browser_feed_not_xml")
    return text


__all__ = [
    "IndustryListingAdapter",
    "SharedFeedAdapter",
    "parse_barrons_ticker_listing",
    "parse_digitimes_listing",
    "parse_shared_feed",
    "parse_trendforce_listing",
]
