"""The Fly's public ticker JSON, with native full bodies and explicit preview limits."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from doxagent.content_enrichment.quality import choose_candidate, inspect_html
from doxagent.monitoring.media_enrichment import MediaExtractionResult

from .schema import (
    AcquisitionMode,
    PollingConfig,
    PollResult,
    RawMessageInput,
    SchedulerConstraints,
    SourceDefinition,
    SourceKind,
    canonical_json,
    sha256_text,
)
from .technology_sources import TechnologyHttp, TechnologySource, plain

SOURCE_ID = "thefly_ticker_news"
API = "https://tfb.thefly.com"
SPEC = TechnologySource(
    SOURCE_ID,
    "The Fly / Ticker News",
    "thefly.com",
    (API + "/news/latest",),
    (),
    route="us-standard-5",
)


def initial_source():
    return SourceDefinition(
        source_id=SOURCE_ID,
        display_name="The Fly / Ticker News",
        kind=SourceKind.CRAWLER,
        adapter_ref="builtin:thefly_public@1",
        acquisition_mode=AcquisitionMode.BY_TICKER,
        entry_url="https://www.thefly.com/",
        content_language="en",
        scheduler_group="technology.thefly.com",
        scheduler_constraints=SchedulerConstraints(
            max_concurrency=2, minimum_request_gap_seconds=0.5
        ),
        default_polling_config=PollingConfig(target_interval_seconds=60, active_windows=[]),
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )


def published(date):
    return (
        datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .astimezone(UTC)
    )


def article_url(identity, date):
    return API + "/news/get?" + urlencode({"date": date, "id": identity})


def parse_news(raw, ticker):
    data = json.loads(raw)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("news"), list)
        or "accessType" not in data
    ):
        raise ValueError("thefly_news_contract_changed")
    messages = []
    for row in data["news"]:
        if not isinstance(row, dict) or ticker not in row.get("stocks", []):
            continue
        identity, date, title = str(row.get("id", "")), row.get("date"), row.get("title")
        if not identity.isdigit() or not date or not title:
            raise ValueError("thefly_article_identity_missing")
        native = {
            key: row.get(key)
            for key in ("id", "date", "title", "description", "stocks", "topic", "subtype")
        }
        native["access_type"] = row.get("accessType") or data["accessType"]
        version = sha256_text(
            canonical_json([title, plain(row.get("description")), row.get("topic")])
        )
        messages.append(
            RawMessageInput(
                external_id=identity,
                source_item_key=identity,
                title=plain(title),
                summary=plain(row.get("description")) or None,
                body=None,
                source="The Fly",
                publisher_name="The Fly",
                url=article_url(identity, date),
                published_at=published(date),
                publication_time_basis="EXACT",
                raw_payload={"fly_article": native, "updated_at": version},
                metadata={
                    "provider": "thefly_public",
                    "language": "en",
                    "content_language": "en",
                    "publisher_domain": "thefly.com",
                    "body_access_scope": "public:thefly",
                    "identity_evidence": {
                        "id_kind": "stable_article",
                        "url_kind": "article",
                        "mutable": True,
                        "provider_version": version,
                        "language": "en",
                    },
                },
            )
        )
    return data, messages


class TheFlyAdapter:
    def __init__(self, http=None):
        self.http = http or TechnologyHttp()
        self.owns_http = http is None

    async def close(self):
        if self.owns_http:
            await self.http.close()

    async def poll(self, context):
        ticker = context.ticker.strip().upper()
        if not re.fullmatch(r"[A-Z0-9.^-]{1,12}", ticker):
            raise ValueError("thefly_invalid_ticker")
        checkpoint = dict(context.checkpoint)
        seen = dict(checkpoint.get("seen", {}))
        refresh = (
            context.window_cutoff is not None
            or not checkpoint.get("refresh_at")
            or context.requested_at >= datetime.fromisoformat(checkpoint["refresh_at"])
        )
        lower = context.window_start or context.requested_at - timedelta(minutes=30)
        messages = {}
        complete = False
        pages = []
        cursor = None
        for _ in range(4):
            url = API + (
                "/news/more"
                if cursor
                else "/news/latest?" + urlencode({"symbols": ticker, "per_page": 50})
            )
            async with context.request_permit():
                capture = await self.http.fetch(
                    url,
                    SPEC,
                    **(
                        {
                            "method": "POST",
                            "json_body": {"symbols": ticker, "per_page": 50, "since": cursor},
                        }
                        if cursor
                        else {}
                    ),
                )
            if capture.status in {429, 503}:
                from doxagent.content_enrichment.transport import retry_after

                delay = retry_after(capture.headers.get("retry-after")) or 60
                return PollResult(
                    messages=list(messages.values()),
                    next_checkpoint=checkpoint,
                    site_access_deferred=not messages,
                    site_access_deferred_reason="thefly_http_backoff",
                    site_access_retry_not_before=context.requested_at
                    + timedelta(seconds=max(60, delay)),
                    window_coverage="PARTIAL",
                    window_done=False,
                )
            if capture.status != 200:
                raise ValueError(f"thefly_listing_http_{capture.status}")
            data, rows = await asyncio.to_thread(parse_news, capture.text, ticker)
            pages.append(
                {
                    "url": url,
                    "http_status": capture.status,
                    "items": len(rows),
                    "access_type": data["accessType"],
                }
            )
            for message in rows:
                version = message.raw_payload["updated_at"]
                if refresh or seen.get(message.source_item_key) != version:
                    messages[message.source_item_key] = message
                seen[message.source_item_key] = version
            if not rows or len(data["news"]) < 50 or min(m.published_at for m in rows) <= lower:
                complete = True
                break
            next_cursor = min(row["date"] for row in data["news"])
            if next_cursor == cursor:
                break
            cursor = next_cursor
        checkpoint.update(
            seen=dict(list(seen.items())[-1000:]),
            refresh_at=(context.requested_at + timedelta(minutes=5)).isoformat()
            if refresh
            else checkpoint["refresh_at"],
        )
        return PollResult(
            messages=list(messages.values()),
            next_checkpoint=checkpoint,
            window_coverage="COMPLETE" if complete else "PARTIAL",
            window_done=True,
            acquisition_metadata={"pages": pages, "transport": "public_json", "access": "public"},
        )


class TheFlyBodyReader:
    def __init__(self, http):
        self.http = http

    async def extract(self, job, record):
        native = job.message.raw_payload.get("fly_article", {})
        identity = str(job.message.external_id or "")
        if (
            not SPEC.accepts(record.url)
            or not identity.isdigit()
            or record.url != article_url(identity, native.get("date", ""))
            or str(native.get("id")) != identity
            or published(native.get("date", "")) != job.message.published_at
        ):
            raise ValueError("thefly_article_identity_mismatch")
        raw = native.get("description") or ""
        text = plain(raw)
        # The official anonymous API exposes real full wrap-ups, but truncates many
        # premium alerts. Its detail endpoint returns the same preview; do not spend
        # browser/HTTP retries attempting to turn that preview into a full body.
        if (
            native.get("access_type") == "restricted"
            or text.endswith(("...", "…"))
            or re.fullmatch(r"[xX\s]{20,}", text)
        ):
            return MediaExtractionResult(
                record=record,
                reason="subscription_required",
                http_status=200,
                extraction_method="thefly_public_api",
                diagnostics={
                    "outcome": "PARTIAL",
                    "body_source": "publisher_preview",
                    "public_access_type": native.get("access_type"),
                    "publisher_article_id": identity,
                },
            )
        info = await asyncio.to_thread(
            inspect_html,
            "<html><h1>"
            + escape(plain(native.get("title")))
            + "</h1><article>"
            + raw
            + "</article></html>",
            record.url,
            job.message.title,
        )
        candidate, outcome, reason = choose_candidate(info, job.message.title)
        return MediaExtractionResult(
            record=record,
            content=candidate.text if candidate else None,
            http_status=200,
            final_url=record.url,
            reason=None if candidate else reason,
            extraction_method="thefly_native_article_api",
            diagnostics={
                "outcome": outcome,
                "body_source": "publisher_article_api",
                "publisher_article_id": identity,
                "publisher_published_at": job.message.published_at.isoformat(),
                "public_access_type": native.get("access_type"),
                "candidate_summary": [candidate.summary()] if candidate else [],
            },
        )
