"""Public market news listings. No login automation or browser identity overrides."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

from lxml import html as H

from doxagent.site_strategy.client import SiteAccessError

from .industry_sources import _canonical, _listing_guard, _message, _text
from .news_adapters import _failure
from .schema import PollContext, PollResult, RawMessageInput

ET = ZoneInfo("America/New_York")


def parse_investorshub(html: str, *, ticker: str, now: datetime) -> list[RawMessageInput]:
    root = H.fromstring(html)
    _listing_guard(root, html)
    cards = root.xpath(
        '//div[contains(concat(" ",normalize-space(@class)," ")," quote-news-item ")]'
    )
    if not cards:
        raise RuntimeError("investorshub_listing_empty_or_changed")
    rows = {}
    for card in cards:
        links = card.xpath('.//div[contains(@class,"news-content")]/a[@href]')
        if not links:
            links = card.xpath('.//a[contains(@href,"/stock-news/")]')
        for link in links:
            url = urljoin("https://investorshub.advfn.com", link.get("href", ""))
            parsed = urlparse(url)
            match = re.fullmatch(
                r"/stock-market/(?:NASDAQ|NYSE|AMEX)/([^/]+)/stock-news/(\d+)/[^/]+", parsed.path
            )
            market_news = parsed.hostname == "uk.advfn.com" and re.fullmatch(
                r"/market-news/article/(\d+)/[^/]+", parsed.path
            )
            if not market_news and (parsed.hostname != "investorshub.advfn.com" or not match):
                continue
            if match and match[1].rsplit("-", 1)[-1].upper() != ticker.upper():
                continue
            titles = card.xpath(".//h3|.//h2")
            title = _text(titles[0]) if titles else _text(link)
            dates = card.xpath('.//*[contains(@class,"news-date-content")]')
            value = _text(dates[0]) if dates else ""
            exact = card.xpath(".//time/@datetime")
            basis = "DATE"
            try:
                if exact:
                    published = datetime.fromisoformat(exact[0].replace("Z", "+00:00"))
                    published = published.replace(tzinfo=published.tzinfo or ET).astimezone(UTC)
                    basis = "EXACT"
                else:
                    match_date = re.search(r"[A-Z][a-z]+ \d{1,2} \d{4}", value)
                    if match_date:
                        day = datetime.strptime(match_date[0], "%B %d %Y").date()
                    else:
                        relative = re.fullmatch(r"(\d+) (minute|hour|day)s? ago", value)
                        if not relative:
                            continue
                        seconds = (
                            int(relative[1])
                            * {"minute": 60, "hour": 3600, "day": 86400}[relative[2]]
                        )
                        day = (now - timedelta(seconds=seconds)).astimezone(ET).date()
                    # Relative text is only a day hint. Full article metadata,
                    # not crawl time, must establish precise publication time.
                    published = (
                        datetime.combine(day, datetime.min.time(), ET)
                        .replace(hour=12)
                        .astimezone(UTC)
                    )
            except ValueError:
                continue
            if len(title) < 8:
                continue
            summaries = card.xpath(".//p")
            identifier = (
                "advfn-market:" + market_news[1] if market_news else "investorshub:" + match[2]
            )
            rows[identifier] = _message(
                source="InvestorsHub",
                url=_canonical(url),
                title=title,
                summary=_text(summaries[0]) if summaries else None,
                published_at=published,
                basis=basis,
                language="en",
                external_id=identifier,
                payload={"ticker": ticker, "listing_date": value},
            )
    return list(rows.values())


def parse_globenewswire(html: str, *, now: datetime) -> list[RawMessageInput]:
    root = H.fromstring(html)
    _listing_guard(root, html)
    cards = root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," newsLink ")]')
    if not cards and not re.search(
        r"no (?:results|releases|press releases)|0 results", _text(root), re.I
    ):
        raise RuntimeError("globenewswire_listing_empty_or_changed")
    rows = {}
    for card in cards:
        links = card.xpath(
            './/div[contains(@class,"mainLink")]/a[contains(@href,"/news-release/")]'
        )
        dates = card.xpath('.//*[contains(@class,"date-source")]')
        value = _text(dates[0]) if dates else ""
        match = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4} \d{2}:\d{2}) ET", value)
        if not links or not match:
            continue
        url = urljoin("https://www.globenewswire.com", links[0].get("href"))
        if urlparse(url).hostname != "www.globenewswire.com":
            continue
        published = (
            datetime.strptime(match[1], "%B %d, %Y %H:%M").replace(tzinfo=ET).astimezone(UTC)
        )
        summaries = card.xpath(".//p")
        rows[url] = _message(
            source="GlobeNewswire",
            url=url,
            title=_text(links[0]),
            summary=_text(summaries[0]) if summaries else None,
            published_at=published,
            basis="EXACT",
            language="en",
            payload={"listing_date": value},
        )
    return list(rows.values())


def parse_investing(html: str, *, ticker: str, now: datetime) -> list[RawMessageInput]:
    root = H.fromstring(html)
    _listing_guard(root, html)
    # Only the instrument's news list, never navbar/top-stories recommendations.
    scopes = root.xpath('//*[@data-test="instrument-news"]|//*[@data-test="news-list"]')
    if not scopes:
        scopes = root.xpath('//*[@data-test="article-item"]/..')
        scopes = list(dict.fromkeys(scopes))
    rows = {}
    for scope in scopes:
        for card in scope.xpath(".//article"):
            links = card.xpath('.//a[@data-test="article-title-link"]')
            dates = card.xpath(".//time/@datetime")
            if not links or not dates:
                continue
            url = urljoin("https://www.investing.com", links[0].get("href"))
            if urlparse(url).hostname != "www.investing.com" or not re.search(
                r"/news/.+-\d+$", urlparse(url).path
            ):
                continue
            headings = card.xpath(".//h2|.//h3")
            title = _text(headings[0]) if headings else _text(links[0])
            published = datetime.fromisoformat(dates[0].replace("Z", "+00:00"))
            published = published.replace(tzinfo=published.tzinfo or UTC).astimezone(UTC)
            paragraphs = card.xpath(".//p")
            rows[url] = _message(
                source="Investing.com",
                url=url,
                title=title,
                summary=_text(paragraphs[0]) if paragraphs else None,
                published_at=published,
                basis="EXACT",
                language="en",
                payload={"ticker": ticker},
            )
    if not rows:
        raise RuntimeError("investing_instrument_news_empty_or_changed")
    return list(rows.values())


class MarketTickerAdapter:
    def __init__(self, browser: Any, variant: str) -> None:
        self.browser, self.variant = browser, variant

    async def poll(self, context: PollContext) -> PollResult:
        if self.browser is None:
            raise RuntimeError("market listing requires Site Access")
        params = context.binding.source_parameters
        url = str(params.get("listing_url") or "")
        if not url:
            mapping = params.get(
                "ticker_pages", context.source.default_parameters.get("ticker_pages", {})
            )
            url = str(mapping.get(context.ticker.upper(), "")) if isinstance(mapping, dict) else ""
        if not url:
            if self.variant == "investorshub":
                exchange = str(params.get("exchange", "NASDAQ"))
                symbol = quote(context.ticker, safe="")
                url = f"https://investorshub.advfn.com/stock-market/{exchange}/{symbol}/news"
            else:
                raise RuntimeError(
                    "investing_ticker_locator_missing: configure listing_url or ticker_pages"
                )
        expected_host = (
            "investorshub.advfn.com" if self.variant == "investorshub" else "www.investing.com"
        )
        if urlparse(url).hostname != expected_host or urlparse(url).scheme != "https":
            raise ValueError("ticker listing URL must belong to the configured publisher")
        try:
            async with context.request_permit():
                status, final, _, html = await self.browser.get(
                    url,
                    operation_id=context.poll_run_id + ":" + self.variant,
                    remaining_budget_ms=45_000,
                )
        except SiteAccessError as exc:
            if exc.site_access_deferred:
                return PollResult(
                    window_done=False,
                    site_access_deferred=True,
                    site_access_retry_not_before=exc.result.retry_not_before,
                )
            raise
        if status != 200 or urlparse(final).hostname != expected_host:
            raise RuntimeError("market_listing_redirect_or_http_error")
        if self.variant == "investorshub":
            if (
                urlparse(final).path.split("/")[-2].rsplit("-", 1)[-1].upper()
                != context.ticker.upper()
            ):
                raise RuntimeError("investorshub_ticker_identity_mismatch")
            messages = parse_investorshub(html, ticker=context.ticker, now=context.requested_at)
        else:
            if not re.search(
                rf"\b{re.escape(context.ticker)} News\b", _text(H.fromstring(html)), re.I
            ):
                raise RuntimeError("investing_ticker_identity_mismatch")
            messages = parse_investing(html, ticker=context.ticker, now=context.requested_at)
        lower = context.window_start or context.requested_at - timedelta(minutes=30)
        covered = bool(messages) and min(m.published_at for m in messages) < lower
        return PollResult(
            messages=messages,
            window_coverage="COMPLETE" if covered else "PARTIAL",
            acquisition_metadata={"listing_url": final, "provider": self.variant},
        )


class GlobeNewswireSearchAdapter:
    def __init__(self, browser: Any) -> None:
        self.browser = browser

    async def poll(self, context: PollContext) -> PollResult:
        if self.browser is None:
            raise RuntimeError("GlobeNewswire search requires Site Access")
        queries = (context.query_plan or {}).get("queries") or [
            {"query": context.ticker, "query_key": "legacy-ticker"}
        ]
        prior = context.checkpoint.get("queries", {}) if context.is_gap_recovery else {}
        checkpoints, rows, failures = {}, {}, []
        lower = context.window_start or context.requested_at - timedelta(minutes=30)
        max_pages = int(context.binding.source_parameters.get("max_pages", 3))
        deferred, retry_at = False, None
        for query in queries:
            key = str(query["query_key"])
            saved = dict(prior.get(key, {}))
            if saved.get("done"):
                checkpoints[key] = saved
                continue
            page = int(saved.get("page", 1))
            try:
                for _ in range(max_pages):
                    url = (
                        "https://www.globenewswire.com/en/search/keyword/"
                        + quote(str(query["query"]), safe="")
                        + f"/load/more?page={page}&pageSize=10"
                    )
                    async with context.request_permit():
                        status, final, _, html = await self.browser.get(
                            url,
                            operation_id=f"{context.poll_run_id}:{key}:{page}",
                            remaining_budget_ms=45_000,
                        )
                    if status != 200 or urlparse(final).hostname != "www.globenewswire.com":
                        raise RuntimeError("globenewswire_search_redirect_or_http_error")
                    messages = parse_globenewswire(html, now=context.requested_at)
                    rows.update({m.url: m for m in messages})
                    ordered = all(
                        a.published_at >= b.published_at
                        for a, b in zip(messages, messages[1:], strict=False)
                    )
                    complete = len(messages) < 10 or (ordered and messages[-1].published_at < lower)
                    page += 1
                    saved = {
                        "page": page,
                        "done": complete,
                        "coverage": "COMPLETE" if complete else "PARTIAL",
                    }
                    if complete:
                        break
                else:
                    saved["done"] = (
                        True  # Finite recovery; never block healthy sources indefinitely.
                    )
            except SiteAccessError as exc:
                if exc.site_access_deferred:
                    deferred, retry_at = True, exc.result.retry_not_before
                    saved.update(page=page, done=False, coverage="PARTIAL")
                else:
                    count = int(saved.get("failures", 0)) + 1
                    saved.update(page=page, failures=count, done=count >= 2, coverage="PARTIAL")
                    failures.append(
                        _failure(context, "search_query_failed", str(exc), {"query_key": key})
                    )
            except Exception as exc:
                count = int(saved.get("failures", 0)) + 1
                saved.update(page=page, failures=count, done=count >= 2, coverage="PARTIAL")
                failures.append(
                    _failure(context, "search_query_failed", type(exc).__name__, {"query_key": key})
                )
            checkpoints[key] = saved
        return PollResult(
            messages=list(rows.values()),
            failures=failures,
            next_checkpoint={"queries": checkpoints},
            window_done=all(v.get("done") for v in checkpoints.values()),
            window_coverage="COMPLETE"
            if all(v.get("coverage") == "COMPLETE" for v in checkpoints.values())
            else "PARTIAL",
            site_access_deferred=deferred,
            site_access_retry_not_before=retry_at,
            acquisition_metadata={
                "provider": "globenewswire",
                "terms_revision": (context.query_plan or {}).get("terms_revision"),
                "queries": queries,
            },
        )
