"""Publisher-scoped scientific abstract identity and complete-text extraction."""

from urllib.parse import parse_qs, urljoin, urlsplit
import json
import asyncio
import re
from datetime import UTC, datetime
from html import escape

from lxml import html

from doxagent.content_enrichment.quality import (
    Candidate,
    _node_text,
    inspect_html,
    title_match,
    choose_candidate,
)


async def fierce_body(reader, spec, job, record):
    from .biopharma_http import fierce_headers
    from doxagent.monitoring.media_enrichment import MediaExtractionResult

    uid = job.message.raw_payload.get("publisher_article_uuid", "")
    if not re.fullmatch(r"[0-9a-f-]{36}", uid):
        raise ValueError("fierce_article_uuid_missing")
    headers = await fierce_headers(reader, reader.http.fetch, spec, datetime.now(UTC))
    c = await reader.http.fetch(
        "https://www.fiercebiotech.com/jsonapi/node/article/" + uid, spec, headers=headers
    )
    if c.status != 200:
        return MediaExtractionResult(
            record=record,
            http_status=c.status,
            reason="fierce_native_body_http_" + str(c.status),
            diagnostics={"outcome": "UNAVAILABLE"},
        )
    data = json.loads(c.text)["data"]
    a = data["attributes"]
    if data["id"] != uid or urlsplit(record.url).path != a["path"]["alias"]:
        raise ValueError("fierce_body_identity_mismatch")
    if a.get("field_enable_meter_gate") or not a.get("status"):
        return MediaExtractionResult(
            record=record,
            reason="publisher_access_restricted",
            diagnostics={"outcome": "UNAVAILABLE"},
        )
    native = a.get("body") or {}
    content = native.get("processed") or native.get("value")
    if not content:
        raise ValueError("fierce_native_body_missing")
    # Reconstruct only the public article's own documented metadata and full
    # HTML body. Never use a gated body or summarize clinical/economic facts.
    page = (
        '<html><head><link rel="canonical" href="'
        + escape(record.url, quote=True)
        + '"><meta property="og:type" content="article"></head><body><article><h1>'
        + escape(a["title"])
        + "</h1>"
        + content
        + "</article></body></html>"
    )
    info = await asyncio.to_thread(inspect_html, page, record.url, job.message.title)
    candidate, outcome, reason = choose_candidate(info, job.message.title)
    return MediaExtractionResult(
        record=record,
        content=candidate.text if candidate else None,
        final_url=record.url,
        http_status=200,
        reason=None if candidate else reason,
        extraction_method="biopharma_native_api_body",
        diagnostics={
            "outcome": outcome,
            "publisher_published_at": a.get("field_date_published"),
            "body_source": "fierce_public_jsonapi",
        },
    )


def inspect_ash(raw, url, title, **kwargs):
    info = inspect_html(raw, url, title, **kwargs)
    if info.access_reason or info.subscription_article:
        return info
    parsed = urlsplit(url)
    pid = parse_qs(parsed.query).get("pid")
    if (
        parsed.hostname != "onlineprogram.hematology.org"
        or parsed.path != "/paper-details.aspx"
        or not pid
    ):
        return info
    doc = html.fromstring(raw)
    forms = [urlsplit(urljoin(url, v)) for v in doc.xpath("//form/@action")]
    verified = any(
        f.hostname == parsed.hostname
        and f.path == parsed.path
        and parse_qs(f.query).get("pid") == pid
        for f in forms
    )
    bodies = doc.xpath('//*[@id="pnlPaperAbstract"]')
    headlines = [
        n
        for n in doc.xpath('//h2[contains(@class,"session-title")]')
        if title_match(n.text_content(), title)
    ]
    if not verified or len(bodies) != 1 or not headlines:
        return info
    # h1 is the generic "Paper Details". Use the native paper heading and full
    # ABSTRACT panel only after independently checking its stable requested pid.
    info.headline = " ".join(headlines[-1].text_content().split())
    info.canonical = url
    info.canonical_article_match = True
    info.page_kind = "article"
    info.candidates = [
        Candidate(_node_text(bodies[0]), "ash_abstract_dom", True, info.headline, 40)
    ]
    return info
