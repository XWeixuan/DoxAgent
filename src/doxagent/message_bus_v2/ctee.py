"""CTEE semiconductor category, using the list endpoint observed in its live page."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from .schema import PollContext, PollResult, RawMessageInput, SharedPollContext


class CteeSemiconductorAdapter:
    def __init__(self, browser: Any) -> None:
        self.browser = browser

    async def poll(self, context: PollContext) -> PollResult:
        raise RuntimeError("CTEE is a shared distribution entry; use poll_shared")

    async def poll_shared(self, context: SharedPollContext) -> PollResult:
        if self.browser is None:
            raise RuntimeError("CTEE requires Site Access browser")
        page = int(context.checkpoint.get("page", 1))
        max_pages = int(context.source.default_parameters.get("max_pages", 8))
        lower = context.window_start or context.requested_at - timedelta(minutes=30)
        messages: list[RawMessageInput] = []
        old_found = False
        exhausted = False
        pages_fetched = 0
        while page <= max_pages:
            url = f"https://www.ctee.com.tw/api/category/semi/{page}"
            async with context.request_permit():
                status, _final, _headers, html = await self.browser.get(
                    url,
                    operation_id=f"{context.run_id}:ctee:{page}",
                    remaining_budget_ms=45000,
                )
            if status != 200:
                raise RuntimeError(f"CTEE category status {status}")
            rows = parse_category_response(html)
            pages_fetched += 1
            if not rows:
                exhausted = True
                break
            for row in rows:
                message = parse_category_item(row)
                if message is None:
                    continue
                # Date-only entries may be represented by a placeholder 03:00 clock.
                if message.published_at < lower - timedelta(days=1):
                    old_found = True
                    continue
                messages.append(message)
            if old_found or len(rows) < 15:
                exhausted = True
                break
            page += 1
        complete = exhausted or old_found
        return PollResult(
            messages=messages,
            next_checkpoint={"page": page + 1 if complete else page},
            window_coverage="COMPLETE" if complete else "PARTIAL",
            window_done=True,
            acquisition_metadata={
                "provider": "ctee",
                "pages_fetched": pages_fetched,
                "last_page": page,
                "list_endpoint_observed": True,
            },
        )


def parse_category_response(html: str) -> list[dict[str, Any]]:
    text = html.strip()
    wrapper = re.search(r"<body[^>]*>(.*?)</body>", text, re.I | re.S)
    if wrapper:
        text = unescape(wrapper.group(1)).strip()
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError("CTEE category response must be a list")
    return [dict(row) for row in payload if isinstance(row, dict)]


def parse_category_item(row: dict[str, Any]) -> RawMessageInput | None:
    raw_url = str(row.get("hyperLink") or "")
    url = urljoin("https://www.ctee.com.tw", raw_url)
    parsed = urlparse(url)
    if parsed.hostname != "www.ctee.com.tw" or not re.fullmatch(r"/news/\d+-\d+", parsed.path):
        return None
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    date_text = str(row.get("publishDatetime") or "")
    try:
        local = datetime.fromisoformat(date_text).replace(tzinfo=ZoneInfo("Asia/Taipei"))
        basis = "DATE" if local.hour == 3 and local.minute == 0 and local.second == 0 else "EXACT"
        if basis == "DATE":
            local = local.replace(hour=12)
        published_at = local.astimezone(UTC)
    except ValueError:
        published_at = datetime.now(UTC)
        basis = "UNKNOWN_FIRST_SEEN"
    article_id = str(row.get("articleID") or parsed.path.rsplit("/", 1)[-1])
    return RawMessageInput(
        external_id=article_id,
        source_item_key=article_id,
        title=title,
        summary=str(row.get("content") or "").strip() or None,
        body=None,
        source="工商時報",
        publisher_name="工商時報",
        url=url,
        published_at=published_at,
        publication_time_basis=basis,
        raw_payload=row,
        metadata={
            "provider": "ctee",
            "publisher_domain": "ctee.com.tw",
            "content_language": "zh-Hant",
            "identity_evidence": {
                "id_kind": "stable_article",
                "url_kind": "article",
                "mutable": True,
            },
        },
    )
