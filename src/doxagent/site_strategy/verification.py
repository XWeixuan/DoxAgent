"""Pure capture verification shared by maintenance and business extraction."""

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from doxagent.content_enrichment.quality import choose_candidate, inspect_html


def body_verification_url(scope, *candidates, excluded_urls=()):
    """Legacy verification URLs sometimes point at the collector's search page."""
    listing = scope.verification_plan.get("crawler", {}).get("url")

    def key(url):
        parsed = urlsplit(url)
        return parsed.hostname, parsed.path.rstrip("/") or "/"

    excluded = {key(url) for url in (listing, *excluded_urls) if url}

    def collection(url):
        parsed = urlsplit(url)
        return (
            parsed.hostname in {"investing.com", "www.investing.com"}
            and parsed.path.startswith("/equities/")
            and parsed.path.rstrip("/").endswith("-news")
        )

    return next(
        (
            url
            for url in candidates
            if url
            and key(url) not in excluded
            and urlsplit(url).path.rstrip("/")
            and not collection(url)
        ),
        None,
    )


@dataclass
class VerificationReport:
    challenge: str = "UNKNOWN"
    authentication: str = "UNKNOWN"
    crawler: str = "NOT_TESTED"
    body: str = "NOT_TESTED"
    reason: str = "not_tested"
    health_applied: bool = False
    session_closed: bool = False
    cleanup_pending: bool = False
    challenge_task_id: str | None = None
    verification_url: str | None = None
    runtime_instance_id: str | None = None
    crawler_reason: str | None = None
    body_reason: str | None = None
    original_page_kind: str | None = None

    def as_dict(self):
        return asdict(self)


def verification_failure_reason(exc):
    """Actionable bounded diagnostic, without exception URLs, tokens or HTML."""
    match = re.search(r"(?:net::)?ERR_[A-Z_]+", str(exc))
    if match:
        return match.group(0)
    code = getattr(exc, "reason_code", None)
    return code if code and re.fullmatch(r"[A-Za-z0-9_:.-]{1,160}", code) else type(exc).__name__


def validate_capture(
    response,
    resolved,
    *,
    expected_title=None,
    crawler_proof=False,
    expected_subscription=False,
    observed_document=False,
):
    """No networking, publishing, lease acquisition or model requests here."""
    report = VerificationReport()
    info = inspect_html(
        response.body,
        response.final_url,
        expected_title,
        strategy_ref=resolved.body.ref,
        strategy_parameters=resolved.body.parameters,
    )
    if info.access_reason == "challenge_required":
        report.challenge = "BLOCKED"
        report.reason = "challenge_required"
        return report
    if not 200 <= response.status_code < 300 and not (
        observed_document and response.status_code == 0 and response.provenance
    ):
        report.reason = f"verification_http_{response.status_code}"
        return report
    candidate, quality, reason = choose_candidate(info, expected_title)
    report.body = (
        quality
        if candidate
        else (
            "ENTITLEMENT_MISSING"
            if info.access_reason == "subscription_required"
            else "EXTRACTION_ERROR"
        )
    )
    report.reason = reason
    recognized_normal_page = info.page_kind in {"article", "listing", "quote"}
    # A recognized subscription wall has left the WAF document, but is not FULL body.
    if (
        candidate
        or crawler_proof
        or recognized_normal_page
        or info.access_reason
        in {
            "subscription_required",
            "login_required",
        }
    ):
        report.challenge = "CLEARED"
    if crawler_proof:
        report.crawler = "READY"
    if resolved.auth.requirement == "none":
        report.authentication = "NOT_REQUIRED"
    elif info.access_reason == "subscription_required":
        report.authentication = "ENTITLEMENT_MISSING"
    elif info.access_reason == "login_required":
        report.authentication = "REAUTH_REQUIRED"
    elif (
        candidate
        and quality in {"FULL", "SHORT_FULL"}
        and (info.subscription_article or expected_subscription)
    ):
        report.authentication = "VALID"
    # A public article alone cannot prove a paid account has a valid subscription.
    return report


def validate_crawler_capture(source_id, response, request):
    return crawler_capture_items(source_id, response, request) is not None


def crawler_capture_items(source_id, response, request):
    """Use the same pure listing parsers used by the real adapters."""
    from doxagent.message_bus_v2.industry_sources import (
        parse_barrons_ticker_listing,
        parse_digitimes_listing,
        parse_trendforce_listing,
    )
    from doxagent.message_bus_v2.market_sources import (
        parse_globenewswire,
        parse_investing,
        parse_investorshub,
    )

    from .schema import utc_now

    if response.recipe_result is not None and request.recipe_ref in {
        "builtin:reuters_search@1",
        "builtin:yahoo_latest_news@1",
    }:
        rows = response.recipe_result.get("rows")
        return rows if isinstance(rows, list) else None  # Helpers prove explicit empty results.
    now = utc_now()
    if source_id == "ctee_semiconductor":
        from doxagent.message_bus_v2.ctee import parse_category_response, parse_category_item

        if not re.fullmatch(r"/api/category/semi/\d+", urlsplit(request.url).path):
            return None
        try:
            rows = parse_category_response(response.body)
        except (ValueError, TypeError):
            return None
        messages = [message for row in rows if (message := parse_category_item(row)) is not None]
        return messages if messages or not rows else None
    if source_id == "globenewswire_semiconductors_rss":
        from doxagent.message_bus_v2.industry_sources import (
            normalize_browser_feed,
            parse_shared_feed,
        )

        return (
            parse_shared_feed(
                normalize_browser_feed(response.body),
                feed_url=request.url,
                publisher="GlobeNewswire",
                language="en",
                now=now,
            )
            or None
        )
    ticker = str(request.recipe_parameters.get("ticker") or "")
    if not ticker:
        match = re.search(
            r"/stocks/([a-zA-Z0-9.-]+)|/stock-market/[^/]+/[^/]*-([A-Z]+)",
            urlsplit(request.url).path,
        )
        ticker = next((part for part in match.groups() if part), "") if match else ""
    parsers = {
        "trendforce_news": lambda: parse_trendforce_listing(response.body, press=False, now=now),
        "trendforce_press_releases": lambda: parse_trendforce_listing(
            response.body, press=True, now=now
        ),
        "digitimes_semiconductors": lambda: parse_digitimes_listing(response.body, now=now),
        "barrons_ticker_news": lambda: parse_barrons_ticker_listing(
            response.body, ticker=ticker, now=now
        ),
        "investorshub_ticker_news": lambda: parse_investorshub(
            response.body, ticker=ticker, now=now
        ),
        "investing_ticker_news": lambda: parse_investing(response.body, ticker=ticker, now=now),
        "globenewswire_search": lambda: parse_globenewswire(response.body, now=now),
    }
    parser = parsers.get(source_id)
    if parser is None:
        return None  # Unknown contracts must not be certified by a status code.
    return parser() or None
