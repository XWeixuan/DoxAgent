"""Bloomberg Latest's chronological JSON list, without RSS or browser polling."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit

from .schema import AcquisitionFailure, PollResult, RawMessageInput, canonical_json, sha256_text

SOURCE_ID = "bloomberg_massive"
ENTRY_URL = "https://www.bloomberg.com/latest"
# Exact types used by the current Latest > All Latest News client (not a section widget).
STORIES_URL = (
    "https://www.bloomberg.com/lineup-next/api/stories?limit=25&pageNumber={page}"
    "&locale=en&types=ARTICLE%2CFEATURE%2CINTERACTIVE%2CLETTER%2CEXPLAINERS"
)
PAGE_SIZE = 25
MAX_PAGES = 4


def parse_stories(text: str, source) -> list[RawMessageInput]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("bloomberg_latest_empty_or_challenge")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("bloomberg_latest_invalid_story")
        identifier = item.get("id", "")
        headline = item.get("headline", "")
        link = item.get("url", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Z0-9]{14}", identifier):
            raise ValueError("bloomberg_latest_invalid_story_id")
        if not isinstance(headline, str) or not headline.strip() or not isinstance(link, str):
            raise ValueError("bloomberg_latest_missing_title_or_url")
        parts = urlsplit(urljoin(ENTRY_URL, link))
        if (parts.hostname not in {"bloomberg.com", "www.bloomberg.com"}
                or parts.scheme != "https" or parts.username or parts.password
                or not re.match(r"^/(?:news|opinion|features|graphics|newsletters)/", parts.path)):
            raise ValueError("bloomberg_latest_non_article_url")
        url = urlunsplit(("https", "www.bloomberg.com", parts.path, "", ""))
        published = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError("bloomberg_latest_publication_timezone_missing")
        updated = item.get("updatedAt")
        if updated:
            changed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if changed.tzinfo is None:
                raise ValueError("bloomberg_latest_update_timezone_missing")
            updated = changed.astimezone(UTC).isoformat()
        rows.append(RawMessageInput(
            title=unescape(headline).strip(), url=url, source=source.display_name,
            external_id=identifier, source_item_key=identifier, published_at=published,
            # Teasers are evidence, never complete text or a reason to fetch the article here.
            summary=None, body=None,
            raw_payload={"id": identifier, "listing_summary": item.get("summary"),
                         "publishedAt": item["publishedAt"], "updatedAt": item.get("updatedAt"),
                         "type": item.get("type"), "brand": item.get("brand")},
            metadata={"language": item.get("locale") or "en",
                      "publication_time_precision": "second", "discovery_method": "bloomberg_latest_http",
                      "identity_evidence": {"id_kind": "stable_article", "url_kind": "article",
                                            "updated_at": updated}},
        ))
    return rows


def story_hint(row: RawMessageInput) -> str:
    # Preserve publisher-declared revisions; repeated identical timestamps/teasers
    # do not create work, and updatedAt never replaces the original publication time.
    return sha256_text(canonical_json([row.title, row.raw_payload.get("updatedAt")]))


async def poll_latest(adapter, context):
    from .collection_override import CollectionStopped
    from .newswire_listing import cache_evidence
    from .poll_trace import REQUESTS, validate_listing
    from .publisher_http import network_timeout

    source = context.source
    previous = context.checkpoint or {}
    old = previous.get("seen", {})
    events = REQUESTS.get()
    offset = len(events or [])
    started = time.monotonic()
    rows, caches, page_ids = {}, [], set()
    # Bounded shared key, not a random value per request. This is enabled only after
    # comparing actual content freshness against the canonical cached endpoint.
    slot = int(context.requested_at.timestamp() // 30)
    cutoff = context.requested_at - timedelta(minutes=30)
    recipe = {"method": "GET", "routes": ["direct"]}
    failure = None
    try:
        async with network_timeout(12):
            for page in range(1, MAX_PAGES + 1):
                url = STORIES_URL.format(page=page) + "&_=" + str(slot)
                response, _ = await adapter.listing_request(
                    context, url, recipe, {"Accept": "*/*", "Referer": ENTRY_URL})
                cache = cache_evidence(response, url, "GET")
                caches.append(cache)
                if response.status_code != 200:
                    raise ValueError("bloomberg_latest_http_" + str(response.status_code))
                if cache.get("error"):
                    raise ValueError(cache["error"])
                # The source promises near-real-time discovery; old cache is a source
                # failure, even when the response is fast and syntactically valid.
                age = cache.get("validation", {}).get("observed_age_seconds")
                if age is not None and age > 60:
                    raise ValueError("bloomberg_latest_stale_cache")
                parsed = await asyncio.to_thread(parse_stories, response.text, source)
                if not parsed:
                    if page == 1:
                        raise ValueError("bloomberg_latest_empty_head")
                    break
                ids = {r.external_id for r in parsed}
                if ids <= page_ids:
                    raise ValueError("bloomberg_latest_pagination_ignored")
                page_ids.update(ids)
                for row in parsed:
                    row.raw_payload["listing_url"] = url
                    rows.setdefault(row.external_id, row)
                # Scan the complete page. Do not cut off equal/out-of-order timestamps
                # or stop on just its first familiar item.
                if (ids.intersection(old) or len(parsed) < PAGE_SIZE
                        or max(r.published_at for r in parsed) <= cutoff):
                    break
            else:
                raise ValueError("bloomberg_latest_window_overflow")
    except CollectionStopped:
        raise
    except Exception as exc:
        failure = AcquisitionFailure(
            source_id=source.source_id, binding_id="shared:"+source.source_id, ticker="*",
            error_code="BLOOMBERG_LATEST_FAILED", error_message=type(exc).__name__+":"+str(exc)[:260],
            raw_hash=sha256_text(ENTRY_URL), original_payload={"cache": caches})

    seen = dict(old)
    delta = []
    for identifier, row in rows.items():
        hint = story_hint(row)
        # Keep late-appearing candidates in the existing capture/audit path. Normal
        # admission still owns freshness; an old publication is never relabelled new.
        if context.force_refresh or old.get(identifier) != hint:
            delta.append(row)
        seen[identifier] = hint
    ids = [e["physical_request_id"] for e in (events or [])[offset:]
           if e.get("role", "listing") == "listing" and e.get("status_code") == 200]
    proven = validate_listing(events or [], ids, since=offset) if rows and not failure else []
    metadata = {"method": "bloomberg_latest_http", "route": "direct", "cache": caches,
                "pages": len(caches), "feed_items": len(rows), "delta_items": len(delta),
                "request_seconds": round(time.monotonic()-started, 3),
                "validated_request_ids": [e["physical_request_id"] for e in proven]}
    if failure:
        # Keep partial candidates but retry from the last complete checkpoint.
        metadata["error"] = failure.error_message
        return PollResult(messages=delta, next_checkpoint=previous, failures=[failure],
                          window_done=False, acquisition_metadata=metadata)
    return PollResult(messages=delta, next_checkpoint={"seen": dict(list(seen.items())[-2048:])},
                      window_done=True, window_coverage="PARTIAL", acquisition_metadata=metadata)
