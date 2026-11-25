"""Public technology article BODY on its publisher's verified lightweight route."""

import asyncio
from datetime import datetime
from urllib.parse import urlsplit

from doxagent.content_enrichment.publisher_cleanup import clean_text
from lxml import html

from doxagent.content_enrichment.quality import choose_candidate, inspect_html, title_match
from doxagent.content_enrichment.transport import REVALIDATION, conditional_headers, retry_after
from doxagent.monitoring.media_enrichment import FetchAttempt, MediaExtractionResult

from .technology_sources import source_specs, TechnologyHttp


class TechnologyBodyReader:
    def __init__(self, http=None, *, sources=None):
        self.http = http or TechnologyHttp(purpose="BODY")
        self.sources = sources if sources is not None else source_specs()

    async def close(self):
        await self.http.close()

    async def extract(self, job, record):
        if job.source.source_id=='fiercebiotech_biotech':
            from .biopharma_body import fierce_body
            return await fierce_body(self,self.sources[job.source.source_id],job,record)
        if job.source.source_id == 'hkex_akeso' or (job.source.source_id=='fda_release'
                                                   and record.url.lower().endswith('.pdf')):
            from .fintech_body import document_body
            return await document_body(self.http,self.sources[job.source.source_id],record)
        if job.source.adapter_ref == "builtin:thefly_public@1":
            from .thefly_source import TheFlyBodyReader

            return await TheFlyBodyReader(self.http).extract(job, record)
        spec = self.sources[job.source.source_id]
        if not spec.accepts(record.url):
            raise ValueError("technology_body_host_not_approved")
        if spec.source_id == 'cftc_trading_rules':
            from .fintech_body import filing_body
            return await filing_body(self.http,spec,job,record)
        if spec.source_id=='occ_newsroom' and urlsplit(record.url).path.lower().endswith('.pdf'):
            from .fintech_body import document_body
            return await document_body(self.http,spec,record)
        if (spec.source_id in {'sbcamericas_prediction_markets','unchained_crypto'}
            and job.message.raw_payload.get('native_article_url') == record.url
            and job.message.raw_payload.get('native_html')):
            from html import escape
            raw='<article><h1>'+escape(job.message.title)+'</h1>'+job.message.raw_payload['native_html']+'</article>'
            info=await asyncio.to_thread(inspect_html,raw,record.url,job.message.title)
            candidate,outcome,_=choose_candidate(info,job.message.title)
            if candidate and outcome in {'FULL','SHORT_FULL'}:
                return MediaExtractionResult(record=record,content=candidate.text,final_url=record.url,
                    extraction_method='fintech_native_rss',diagnostics={'outcome':outcome,
                    'body_source':'publisher_content_encoded','candidate_summary':[candidate.summary()]})
        capture = await self.http.fetch(record.url, spec, headers=conditional_headers(record.url))
        if capture.status in {403,429} and spec.source_id=='thedefiant_fintech_regulation':
            # Read-only article POST; paid-access schema still gates acceptance.
            capture=await self.http.fetch(record.url,spec,method='POST')
        cached = REVALIDATION.get() or {}
        if (
            capture.status == 304
            and cached.get("content")
            and cached.get("outcome") in {"FULL", "SHORT_FULL"}
        ):
            return MediaExtractionResult(
                record=record,
                content=clean_text(cached["content"], capture.url),
                final_url=capture.url,
                http_status=304,
                extraction_method="http_not_modified",
                diagnostics={
                    "outcome": cached["outcome"],
                    "http_not_modified": clean_text(cached["content"], capture.url) == cached["content"],
                    "body_acquired_at": cached.get("acquired_at"),
                    "validators": cached.get("validators", {}),
                },
            )
        if capture.status == 304:
            capture = await self.http.fetch(record.url, spec)
        if capture.status != 200:
            return MediaExtractionResult(
                record=record,
                http_status=capture.status,
                final_url=capture.url,
                reason=f"technology_http_{capture.status}",
                attempts=[
                    FetchAttempt(
                        url=record.url,
                        phase="direct",
                        status_code=capture.status,
                        reason=f"http_{capture.status}",
                        retry_after_seconds=retry_after(capture.headers.get("retry-after")),
                    )
                ],
                diagnostics={"outcome": "UNAVAILABLE", "body_source": "technology_http"},
            )
        inspector=inspect_html
        if spec.source_id=='ash_cullinan':
            from .biopharma_body import inspect_ash
            inspector=inspect_ash
        if spec.source_id in {'rg_prediction_markets','ledgerinsights_news'}:
            from functools import partial
            from .fintech_body import inspect_article
            inspector=partial(inspect_article,spec)
        info = await asyncio.to_thread(
            inspector,
            capture.text,
            record.url,
            job.message.title,
            strategy_parameters={
                "body_xpath": [
                    '//*[@itemprop="articleBody"]',
                    '//*[contains(concat(" ",normalize-space(@class)," ")," entry-content ")]',
                    '//*[contains(concat(" ",normalize-space(@class)," "),'
                    '" single-post-content ")]',
                ]
            },
        )
        if ((spec.source_id=='theblock_crypto' and job.message.raw_payload.get('publisher_pro'))
                or job.message.raw_payload.get('publisher_subscription')):
            info.subscription_article=True
        # A public session has no paid entitlement; previews retain their access reason.
        if info.subscription_article:
            info.access_reason = "subscription_required"
        publisher_title = None
        if spec.domain == "siliconangle.com" and not title_match(
            info.headline, job.message.title or ""
        ):
            # This publisher sometimes revises its headline before its RSS cache.
            # Accept that revision only with the exact canonical URL, article type,
            # and original publication instant; an unrelated page cannot qualify.
            doc = html.fromstring(capture.text)
            titles = doc.xpath('//meta[@property="og:title"]/@content')
            dates = doc.xpath('//meta[@property="article:published_time"]/@content')
            article_type = doc.xpath('//meta[@property="og:type"]/@content')
            try:
                stamp = datetime.fromisoformat(dates[0].replace("Z", "+00:00")) if dates else None
            except ValueError:
                stamp = None
            if (
                titles
                and stamp
                and stamp.tzinfo
                and article_type == ["article"]
                and doc.xpath(
                    '//*[contains(concat(" ",normalize-space(@class)," ")," single-post-content ")]'
                )
                and spec.accepts(info.canonical)
                and urlsplit(info.canonical).path.rstrip("/")
                == urlsplit(record.url).path.rstrip("/")
                and stamp == job.message.published_at
            ):
                publisher_title = titles[0].removesuffix(" - SiliconANGLE")
                info.headline = publisher_title
                info.canonical_article_match = True
                info.page_kind = "article"
                info.publisher_published_at = stamp.isoformat()
        candidate, outcome, reason = choose_candidate(info, job.message.title)
        return MediaExtractionResult(
            record=record,
            content=candidate.text if candidate else None,
            final_url=capture.url,
            http_status=200,
            extraction_method="technology_public_http",
            reason=None if candidate else reason,
            attempts=[
                FetchAttempt(
                    url=record.url,
                    phase="direct",
                    status_code=200,
                    reason=None if candidate else reason,
                )
            ],
            diagnostics={
                "outcome": outcome,
                "body_source": "technology_public_http",
                "http_route": spec.route,
                "pipeline_version": "technology_public_v1",
                "publisher_published_at": info.publisher_published_at,
                "publisher_title": publisher_title,
                "validators": {
                    k: v for k, v in capture.headers.items() if k in {"etag", "last-modified"}
                },
                "candidate_summary": [candidate.summary()] if candidate else [],
            },
        )
