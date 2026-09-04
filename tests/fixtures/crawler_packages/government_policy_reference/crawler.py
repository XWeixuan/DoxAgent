from __future__ import annotations

import re
from urllib.parse import urljoin

from doxagent.crawler_plane.schema import (
    CrawlerItemFailure,
    CrawlerObservation,
    CrawlerRunOutput,
)
from doxagent.crawler_plane.toolkit import (
    advance_seen_ids,
    article_text,
    parse_datetime,
    unseen_ids,
)
from doxagent.crawler_plane.worker_runtime import CrawlerContext

ITEM = re.compile(
    r'<a\s+[^>]*data-document-id="(?P<id>[^"]+)"[^>]*data-revision="(?P<revision>[^"]+)"'
    r'[^>]*data-published="(?P<published>[^"]+)"[^>]*href="(?P<href>[^"]+)"[^>]*>'
    r"(?P<title>.*?)</a>",
    flags=re.I | re.S,
)


async def crawl(ctx: CrawlerContext) -> CrawlerRunOutput:
    listing_url = str(ctx.parameters["listing_url"])
    listing = await ctx.browser.get(listing_url)
    listing.raise_for_status()
    rows_by_key = {
        f"{row['id']}:{row['revision']}": row
        for row in (match.groupdict() for match in ITEM.finditer(listing.text))
    }
    rows = list(rows_by_key.values())
    keys = [f"{row['id']}:{row['revision']}" for row in rows]
    new_keys = unseen_ids(keys, ctx.checkpoint, key="seen_revisions")
    observations: list[CrawlerObservation] = []
    item_failures: list[CrawlerItemFailure] = []
    completed_retry_keys: list[str] = []

    async def process(row: dict[str, str], *, retry: bool) -> None:
        item_key = f"{row['id']}:{row['revision']}"
        url = urljoin(listing_url, row["href"])
        try:
            detail = await ctx.http.get(url)
            detail.raise_for_status()
        except Exception as exc:
            item_failures.append(
                CrawlerItemFailure(
                    item_key=item_key,
                    stage="detail",
                    url=url,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    retry_payload=row,
                )
            )
            return
        try:
            observations.append(
                CrawlerObservation(
                    external_id=item_key,
                    title=article_text(row["title"]),
                    body=article_text(detail.text),
                    source=str(ctx.parameters["source_name"]),
                    url=url,
                    published_at=parse_datetime(row["published"]),
                    metadata={
                        "document_id": row["id"],
                        "revision": row["revision"],
                        "from_retry": retry,
                    },
                )
            )
        except Exception as exc:
            item_failures.append(
                CrawlerItemFailure(
                    item_key=item_key,
                    stage="parse",
                    url=url,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    retry_payload=row,
                )
            )
            return
        if retry:
            completed_retry_keys.append(item_key)

    retry_keys: set[str] = set()
    for retry_item in ctx.retry_items:
        item_key = str(retry_item["item_key"])
        retry_keys.add(item_key)
        payload = {str(key): str(value) for key, value in retry_item["retry_payload"].items()}
        await process(payload, retry=True)
    for row, key in zip(rows, keys, strict=True):
        if key not in new_keys or key in retry_keys:
            continue
        await process(row, retry=False)
    return CrawlerRunOutput(
        observations=observations,
        item_failures=item_failures,
        completed_retry_keys=completed_retry_keys,
        next_checkpoint=advance_seen_ids(ctx.checkpoint, keys, key="seen_revisions"),
        diagnostics={
            "listing_count": len(rows),
            "revision_count": len(new_keys),
            "retry_count": len(ctx.retry_items),
        },
    )
