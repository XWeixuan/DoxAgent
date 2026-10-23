"""Barron's stock-news capture using its native same-origin browser feed.

The quote UI lazy-loads news and renders publication labels separately. Neither
scroll stability nor hydrated date labels are a prerequisite for reading the
same public JSON the two news tabs consume. TLS, cookies and proxy remain Chrome's.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from html import escape
from urllib.parse import quote, urlsplit


class BarronsNewsFeedError(RuntimeError):
    failure_category = "CONTENT_ERROR"

    def __init__(self, reason: str):
        self.reason_code = reason
        super().__init__(reason)


CHANNELS = {"Barrons": "barrons", "BarronsDowJonesNetwork": "other"}


def render_news_feeds(feeds: list[dict], *, channels=None) -> str:
    """Validate both tab contracts before emitting HTML for the existing parser."""
    sections = []
    channels = list(CHANNELS) if channels is None else list(channels)
    if not channels or any(c not in CHANNELS for c in channels) or len(feeds) != len(channels):
        raise BarronsNewsFeedError("barrons_news_feed_incomplete")
    for feed, channel in zip(feeds, channels, strict=True):
        section = CHANNELS[channel]
        status = feed.get("status", 0)
        if status != 200:
            error = BarronsNewsFeedError(f"barrons_news_feed_http_{int(status)}")
            error.failure_category = (
                "AUTH_OR_ACCESS_UNKNOWN"
                if status in {401, 403}
                else "ACCESS_RATE_LIMIT"
                if status == 429
                else "TRANSIENT_TRANSPORT"
            )
            raise error
        try:
            payload = json.loads(feed["text"])
        except (ValueError, TypeError, KeyError) as exc:
            raise BarronsNewsFeedError("barrons_news_feed_invalid_json") from exc
        blocks = payload.get("blocks") if isinstance(payload, dict) else None
        if not isinstance(blocks, list) or not blocks:
            raise BarronsNewsFeedError("barrons_news_feed_contract_changed")
        cards = []
        for block in blocks:
            if (
                not isinstance(block, dict)
                or block.get("$type") != "News.MoreHeadlinesCard"
                or block.get("contentFeed") != channel
                or not isinstance(block.get("blocks"), list)
            ):
                raise BarronsNewsFeedError("barrons_news_feed_contract_changed")
            for item in block["blocks"]:
                if not isinstance(item, dict) or item.get("$type") != "News.StoryCard":
                    raise BarronsNewsFeedError("barrons_news_card_contract_changed")
                url, title = item.get("url"), item.get("headline")
                stamp = item.get("timestampUtc", {})
                stamp = stamp.get("value") if isinstance(stamp, dict) else None
                try:
                    date = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except (AttributeError, ValueError, TypeError):
                    raise BarronsNewsFeedError("barrons_news_publication_time_missing") from None
                if date.tzinfo is None or not isinstance(url, str) or not isinstance(title, str):
                    raise BarronsNewsFeedError("barrons_news_card_contract_changed")
                # The existing parser enforces publisher/domain/section rules,
                # excludes IBD and preserves exact-time freshness admission.
                summary = item.get("summary") or ""
                cards.append(
                    f'<article><time datetime="{escape(date.isoformat(), quote=True)}"></time>'
                    f'<a href="{escape(url, quote=True)}">{escape(title)}</a>'
                    f"<p>{escape(str(summary))}</p></article>"
                )
        sections.append(
            f'<section data-dj-section="{section}" data-feed-complete="true">'
            + "".join(cards)
            + "</section>"
        )
    return (
        '<html><title>Barron\'s ticker news</title><main data-barrons-feed="complete" '
        f'data-feed-channels="{",".join(CHANNELS[c] for c in channels)}">'
        + "".join(sections)
        + "</main></html>"
    )


async def capture_barrons_ticker_news(page, url: str, *, timeout_seconds: float = 12,
                                    channel: str | None = None) -> str:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/market-data/stocks/([A-Za-z0-9.^-]+)/?", parsed.path)
    if parsed.hostname != "www.barrons.com" or not match:
        raise BarronsNewsFeedError("barrons_news_invalid_ticker_url")
    ticker = quote(match.group(1).lower(), safe="")
    channels = list(CHANNELS) if channel is None else [channel]
    if any(c not in CHANNELS for c in channels):
        raise BarronsNewsFeedError("barrons_news_invalid_channel")
    paths = [
        f"/market-data/api/news?channel={channel}&chartingSymbol=stock%2F%2F%2F{ticker}"
        "&pageNumber=0&count=40"
        for channel in channels
    ]
    async with asyncio.timeout(timeout_seconds):
        feeds = await page.evaluate(
            """async paths => {
              if(location.origin!=='https://www.barrons.com')
                throw new Error('barrons_news_origin_mismatch');
              return await Promise.all(paths.map(async path => {
                const controller=new AbortController();
                const timer=setTimeout(()=>controller.abort(),8000);
                try {
                  const r=await fetch(path,{credentials:'include',signal:controller.signal});
                  return {status:r.status,text:await r.text()};
                } finally {clearTimeout(timer);}
              }));
            }""",
            paths,
        )
    return render_news_feeds(feeds, channels=channels)
