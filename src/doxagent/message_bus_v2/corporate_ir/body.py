"""Public article completion with frozen endpoint recipes, no Site Access provisioning."""

import asyncio
import html as html_stdlib
import json
from datetime import datetime
from urllib.parse import urlsplit

from lxml import html

from doxagent.content_enrichment.quality import choose_candidate, inspect_html
from doxagent.monitoring.media_enrichment import MediaExtractionResult

from .http import PublicHttp
from .schema import IrEndpointSpec


def inspect_article(raw, url, title):
    if urlsplit(url).hostname == "www.constellationenergy.com":
        doc = html.fromstring(raw)
        scopes = doc.xpath(
            '//div[contains(@class,"ce-container") and '
            'contains(@class,"aem-GridColumn--default--10")]'
        )
        if len(scopes) == 1:
            # The release column ends before media enquiries and alert forms.
            raw = "<article>" + html.tostring(scopes[0], encoding="unicode") + "</article>"
    info = inspect_html(raw, url, title)
    candidate, quality, reason = choose_candidate(info, title)
    return candidate, quality, reason


def exact_publication(raw, endpoint):
    # Date-only publisher templates must not gain synthetic midnight precision.
    if endpoint.endpoint_id in {"nbis.press", "rklb.updates", "snow.press"}:
        return None
    doc = html.fromstring(raw)

    def walk(value):
        if isinstance(value, dict):
            if value.get("@type") in {"NewsArticle", "Article"} and value.get("datePublished"):
                try:
                    date = datetime.fromisoformat(value["datePublished"].replace("Z", "+00:00"))
                    if date.tzinfo:
                        return date.isoformat()
                except (ValueError, TypeError):
                    pass
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return None

    for raw_json in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            found = walk(json.loads(raw_json))
            if found:
                return found
        except ValueError:
            pass
    return None


class IrBodyReader:
    def __init__(self, http=None):
        self.http = http or PublicHttp()
        self.slots = asyncio.Semaphore(4)

    async def extract(self, job, record):
        spec = IrEndpointSpec.model_validate(job.message.metadata["ir_endpoint_spec"])
        if spec.owner_ticker != job.binding.ticker:
            raise ValueError("IR frozen body endpoint owner mismatch")
        if urlsplit(record.url).hostname not in spec.allowed_article_hosts:
            raise ValueError("IR body host not approved")
        raw = job.message.raw_payload.get("feed_html")
        # Feed HTML must describe this article, not a separate link/teaser.
        if (
            spec.endpoint_id in {"onds.press", "rklb.ir"}
            and job.message.metadata.get("ir_body_field") in {"content:encoded", "description"}
            and isinstance(raw, str)
            and raw
            and job.message.raw_payload.get("article_url") == job.message.url
        ):
            candidate, quality, _ = await asyncio.to_thread(
                inspect_article,
                "<article><h1>"
                + html_stdlib.escape(job.message.title or "")
                + "</h1>"
                + raw
                + "</article>",
                record.url,
                job.message.title,
            )
            if (
                candidate
                and quality in {"FULL", "SHORT_FULL"}
                and not candidate.text.rstrip().endswith(("...", "…"))
            ):
                return MediaExtractionResult(
                    record=record,
                    content=candidate.text,
                    final_url=record.url,
                    extraction_method="ir_native_feed_article",
                    diagnostics={
                        "outcome": quality,
                        "body_source": job.message.metadata.get("ir_body_field"),
                        "candidate_summary": [candidate.summary()],
                    },
                )
        async with self.slots:
            capture = await self.http.fetch(spec, url=record.url)
        if capture.status != 200:
            return MediaExtractionResult(
                record=record, reason=f"ir_http_{capture.status}", http_status=capture.status
            )
        candidate, quality, reason = await asyncio.to_thread(
            inspect_article, capture.body, capture.url, job.message.title
        )
        diagnostics = {
            "outcome": quality,
            "body_source": "ir_public_http",
            "publisher_published_at": await asyncio.to_thread(
                exact_publication, capture.body, spec
            ),
        }
        if candidate:
            diagnostics["candidate_summary"] = [candidate.summary()]
        return MediaExtractionResult(
            record=record,
            content=candidate.text if candidate else None,
            final_url=capture.url,
            http_status=capture.status,
            reason=None if candidate else reason,
            extraction_method="ir_public_http",
            diagnostics=diagnostics,
        )

    async def close(self):
        await self.http.close()
