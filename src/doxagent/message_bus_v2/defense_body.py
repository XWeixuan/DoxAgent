"""Public defense publisher BODY completion on the same verified HTTP route as discovery."""

import asyncio
from dataclasses import replace
from html import escape
from urllib.parse import urlsplit

from lxml import html

from doxagent.content_enrichment.quality import choose_candidate, inspect_html
from doxagent.content_enrichment.transport import REVALIDATION, conditional_headers, retry_after
from doxagent.monitoring.media_enrichment import FetchAttempt, MediaExtractionResult

from .defense_sources import ENDPOINTS, DefenseHttp


class DefenseBodyReader:
    def __init__(self, http=None):
        self.http = http or DefenseHttp()

    async def close(self):
        await self.http.close()

    async def extract(self, job, record):
        e = ENDPOINTS[job.source.source_id]
        if e.body_route:
            e = replace(e, route=e.body_route)
        if urlsplit(record.url).hostname != e.host:
            raise ValueError("defense_body_host_not_approved")
        payload = job.message.raw_payload
        # NASA's official API supplies the full article field; RSS descriptions
        # and search snippets never qualify as native bodies.
        native = (
            e.parser == "nasa"
            and payload.get("article_url") == record.url
            and payload.get("article_html")
        )
        capture = None
        if native:
            raw = (
                "<article><h1>"
                + escape(job.message.title or "")
                + "</h1>"
                + payload["article_html"]
                + "</article>"
            )
        else:
            capture = await self.http.fetch(record.url, e, headers=conditional_headers(record.url))
            cached = REVALIDATION.get() or {}
            if (
                capture.status == 304
                and cached.get("content")
                and cached.get("outcome") in {"FULL", "SHORT_FULL"}
            ):
                return MediaExtractionResult(
                    record=record,
                    content=cached["content"],
                    final_url=capture.url,
                    http_status=304,
                    extraction_method="http_not_modified",
                    diagnostics={
                        "outcome": cached["outcome"],
                        "http_not_modified": True,
                        "body_acquired_at": cached.get("acquired_at"),
                        "validators": cached.get("validators", {}),
                    },
                )
            if capture.status != 200:
                return MediaExtractionResult(
                    record=record,
                    reason=f"defense_http_{capture.status}",
                    http_status=capture.status,
                    attempts=[
                        FetchAttempt(
                            url=record.url,
                            phase="direct",
                            status_code=capture.status,
                            reason=f"http_{capture.status}",
                            retry_after_seconds=retry_after(capture.headers.get("retry-after")),
                        )
                    ],
                )
            raw = capture.text
        body_xpath = {
            "breakingdefense_space": [
                '//div[contains(concat(" ",normalize-space(@class)," ")," post-single__content ")]'
            ],
            "calcalist_search": ['//*[@id="ArticleBodyComponent"]'],
        }.get(e.source_id, [])
        if body_xpath and not any(html.fromstring(raw).xpath(selector) for selector in body_xpath):
            return MediaExtractionResult(
                record=record,
                reason="article_body_scope_missing",
                http_status=capture.status if capture else None,
                diagnostics={"outcome": "PARTIAL", "body_source": "defense_public_http"},
            )
        info = await asyncio.to_thread(
            inspect_html,
            raw,
            record.url,
            job.message.title,
            strategy_parameters={"body_xpath": body_xpath},
        )
        if e.source_id == "shephard_uncrewed" and info.subscription_article:
            # Public HTTP has no verified subscriber entitlement. A long
            # preview must not pass merely because it exceeds 800 characters.
            info.access_reason = "subscription_required"
        if job.message.metadata.get("defense_media_format") == "video_or_podcast":
            info.page_kind = "media"
        candidate, outcome, reason = choose_candidate(info, job.message.title)
        diagnostics = {
            "outcome": outcome,
            "body_source": "official_article_api" if native else "defense_public_http",
            "http_route": e.route,
            "pipeline_version": "defense_public_v1",
            "publisher_published_at": info.publisher_published_at,
            "public_access_free": not info.subscription_article,
            "validators": {
                k.lower(): v
                for k, v in (capture.headers if capture else {}).items()
                if k.lower() in {"etag", "last-modified"}
            },
        }
        if candidate:
            diagnostics["candidate_summary"] = [candidate.summary()]
        return MediaExtractionResult(
            record=record,
            content=candidate.text if candidate else None,
            final_url=capture.url if capture else record.url,
            http_status=capture.status if capture else None,
            reason=None if candidate else reason,
            extraction_method="defense_official_article_api" if native else "defense_public_http",
            diagnostics=diagnostics,
            attempts=[]
            if native
            else [
                FetchAttempt(
                    url=record.url,
                    phase="direct",
                    status_code=200,
                    reason=None if candidate else reason,
                )
            ],
        )
