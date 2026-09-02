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
    r'<a\s+[^>]*data-id="(?P<id>[^"]+)"[^>]*data-published="(?P<published>[^"]+)"'
    r'[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    flags=re.I | re.S,
)


async def crawl(ctx: CrawlerContext) -> CrawlerRunOutput:
    listing_url = str(ctx.parameters["listing_url"])
    listing = await ctx.http.get(listing_url)
    listing.raise_for_status()
    rows = [match.groupdict() for match in ITEM.finditer(listing.text)]
    new_ids = unseen_ids([row["id"] for row in rows], ctx.checkpoint)
    observations = []
    for row in rows:
        if row["id"] not in new_ids:
            continue
        url = urljoin(listing_url, row["href"])
        detail = await ctx.http.get(url)
        detail.raise_for_status()
        observations.append(
            CrawlerObservation(
                external_id=row["id"],
                title=article_text(row["title"]),
                body=article_text(detail.text),
                source=str(ctx.parameters["source_name"]),
                url=url,
                published_at=parse_datetime(row["published"]),
                metadata={"document_id": row["id"]},
            )
        )
    return CrawlerRunOutput(
        observations=observations,
        next_checkpoint=advance_seen_ids(ctx.checkpoint, [row["id"] for row in rows]),
        diagnostics={"listing_count": len(rows), "new_count": len(new_ids)},
    )
