"""Bounded native DOM observation for Investing's ad-heavy background documents."""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from doxagent.content_enrichment.quality import choose_candidate, inspect_html

logger = logging.getLogger(__name__)


def listing_ticker(html, url):
    """Business proof from this exact ticker document, not a script/ad marker."""
    from lxml import html as H
    from lxml.etree import ParserError

    from doxagent.message_bus_v2.market_sources import parse_investing

    if urlsplit(url).hostname not in {"investing.com", "www.investing.com"}:
        return None
    if inspect_html(html, url, "", generic_extraction=False).access_reason:
        return None
    try:
        for node in H.fromstring(html).xpath("//h1|//h2[not(ancestor::article)]"):
            match = re.fullmatch(
                r"([A-Z0-9.-]+)\s+News", " ".join(" ".join(node.itertext()).split())
            )
            if match and parse_investing(html, ticker=match[1], now=datetime.now(UTC)):
                return match[1]
    except (ValueError, ParserError):
        pass
    return None


async def response_listing(response, url, ticker, *, timeout, max_bytes):
    """Observe this exact completed native response, never fetch another URL."""
    if response is None or not 200 <= response.status < 300 or response.url != url or not ticker:
        return None
    from lxml import html as H

    from doxagent.message_bus_v2.market_sources import parse_investing

    try:
        async with asyncio.timeout(timeout):
            raw = await response.body()  # Playwright observes Network.getResponseBody.
        if len(raw) > max_bytes:
            return None
        html = raw.decode("utf-8")
        if inspect_html(html, url, "", generic_extraction=False).access_reason:
            return None
        headings = H.fromstring(html).xpath("//h1|//h2[not(ancestor::article)]")
        if not any(
            re.fullmatch(
                rf"{re.escape(ticker)}\s*News", " ".join(" ".join(n.itertext()).split()), re.I
            )
            for n in headings
        ):
            return None
        if not parse_investing(html, ticker=ticker, now=datetime.now(UTC)):
            return None
        return html
    except Exception:
        return None  # Keep the actual incomplete DOM and existing failure classification.


async def capture_document(
    cdp,
    target_id,
    url,
    *,
    timeout,
    body_parameters=None,
    challenge_grace_seconds=25,
    on_challenge=None,
):
    """No JS evaluation, interception, resource blocking, navigation or CAPTCHA clicks."""
    deadline = time.monotonic() + timeout
    session_id = None
    last = ""
    final_url = url
    challenge_since = None
    started = time.monotonic()
    reads = 0
    first_challenge = None
    last_kind = "unknown"
    try:
        async with asyncio.timeout_at(deadline):
            attached = await cdp.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            session_id = attached["sessionId"]
            while True:
                try:
                    root = (await cdp.send("DOM.getDocument", {"depth": 0}, session_id=session_id))[
                        "root"
                    ]
                    last = (
                        await cdp.send(
                            "DOM.getOuterHTML", {"nodeId": root["nodeId"]}, session_id=session_id
                        )
                    )["outerHTML"]
                except RuntimeError as exc:
                    message = str(exc).casefold()
                    if any(
                        marker in message
                        for marker in (
                            "could not find node",
                            "no node with given id",
                            "cannot find context",
                            "document is not available",
                            "no document",
                        )
                    ):
                        # A native WAF redirect can replace the document between
                        # these two observations. Retry the DOM read, not navigation.
                        await asyncio.sleep(0.1)
                        continue
                    logger.warning("investing native DOM snapshot failed: %s", str(exc)[:300])
                    raise
                final_url = root.get("documentURL") or url
                if final_url.startswith("about:"):
                    await asyncio.sleep(0.25)
                    continue
                if urlsplit(final_url).hostname not in {"investing.com", "www.investing.com"}:
                    return last, final_url
                info = inspect_html(
                    last,
                    final_url,
                    "",
                    strategy_parameters=body_parameters,
                    generic_extraction=False,
                )
                reads += 1
                last_kind = info.page_kind
                if info.access_reason == "challenge_required":
                    if challenge_since is None:
                        challenge_since = time.monotonic()
                        if first_challenge is None:
                            first_challenge = challenge_since
                        if on_challenge is not None:
                            await on_challenge(last, final_url)
                    if time.monotonic() - challenge_since >= challenge_grace_seconds:
                        return last, final_url
                elif info.access_reason or info.page_kind == "listing":
                    return last, final_url
                else:
                    challenge_since = None
                candidate, quality, _ = choose_candidate(info, "")
                if candidate and quality in {"FULL", "SHORT_FULL"}:
                    return last, final_url
                if time.monotonic() + 0.5 >= deadline:
                    return last, final_url  # Preserve the actual incomplete document.
                await asyncio.sleep(0.5)
    except TimeoutError:
        if last:
            return last, final_url
        raise
    finally:
        logger.info(
            "investing_native_capture target=%s elapsed=%.3f reads=%s first_challenge_s=%s kind=%s",
            target_id,
            time.monotonic() - started,
            reads,
            round(first_challenge - started, 3) if first_challenge is not None else None,
            last_kind,
        )
        if session_id:
            try:
                async with asyncio.timeout(1):
                    await cdp.send("Target.detachFromTarget", {"sessionId": session_id})
            except Exception:
                pass
