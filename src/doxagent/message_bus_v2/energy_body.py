"""Public publisher BODY and complete, isolated NRC event-report snapshots."""

import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from lxml import html

from doxagent.monitoring.media_enrichment import MediaExtractionResult

from .energy_catalog import SOURCES
from .energy_sources import EnergyHttp
from .schema import sha256_text
from .technology_body import TechnologyBodyReader


class EnergyBodyReader(TechnologyBodyReader):
    def __init__(self, http=None):
        super().__init__(EnergyHttp(http), sources=SOURCES)

    async def extract(self, job, record):
        if job.source.source_id == "nrc_event_notifications":
            payload = job.message.raw_payload
            event = payload.get("event_id", "")
            raw = payload.get("event_html", "")
            if (
                not re.fullmatch(r"\d+", event)
                or not SOURCES[job.source.source_id].accepts(record.url)
                or job.message.external_id != "nrc-en-" + event
                or parse_qs(urlsplit(record.url).query).get("event") != [event]
                or sha256_text(raw) != payload.get("content_hash")
            ):
                raise ValueError("nrc_native_event_identity_mismatch")
            doc = html.fromstring("<article>" + raw + "</article>")
            if len(doc.xpath(".//div[@id=$id]", id="en" + event)) != 1 or not doc.xpath(
                './/b[normalize-space(.)="Event Text"]'
            ):
                raise ValueError("nrc_native_event_incomplete")
            content = "\n".join(part.strip() for part in doc.itertext() if part.strip())
            return MediaExtractionResult(
                record=record,
                content=content,
                final_url=record.url,
                extraction_method="nrc_native_event_report",
                diagnostics={
                    "outcome": "FULL" if len(content) >= 800 else "SHORT_FULL",
                    "body_source": "nrc_native_event_report",
                    "identity_match": "official_event_number",
                    "pipeline_version": "energy_public_v1",
                    "report_url": payload["report_url"],
                },
            )
        result = await super().extract(job, record)
        return replace(
            result,
            extraction_method=result.extraction_method
            if result.http_status == 304
            else "energy_public_http",
            diagnostics={
                **result.diagnostics,
                "body_source": "energy_public_http",
                "pipeline_version": "energy_public_v1",
            },
        )
