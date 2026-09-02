from __future__ import annotations

import re
from urllib.parse import urljoin

from doxagent.crawler_plane.schema import CrawlerObservation, CrawlerRunOutput
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
    rows = [match.groupdict() for match in ITEM.finditer(listing.text)]
    keys = [f"{row['id']}:{row['revision']}" for row in rows]
    new_keys = unseen_ids(keys, ctx.checkpoint, key="seen_revisions")
    observations = []
    for row, key in zip(rows, keys, strict=True):
        if key not in new_keys:
            continue
        url = urljoin(listing_url, row["href"])
        detail = await ctx.http.get(url)
        detail.raise_for_status()
        observations.append(
            CrawlerObservation(
                external_id=key,
                title=article_text(row["title"]),
                body=article_text(detail.text),
                source=str(ctx.parameters["source_name"]),
                url=url,
                published_at=parse_datetime(row["published"]),
                metadata={"document_id": row["id"], "revision": row["revision"]},
            )
        )
    return CrawlerRunOutput(
        observations=observations,
        next_checkpoint=advance_seen_ids(ctx.checkpoint, keys, key="seen_revisions"),
        diagnostics={"listing_count": len(rows), "revision_count": len(new_keys)},
    )
