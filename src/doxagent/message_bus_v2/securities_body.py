"""Complete public economic documents and tZERO's original published CMS body."""

import asyncio
import json
import re
from dataclasses import replace
from hashlib import sha256
from html import escape
from urllib.parse import urlencode, urljoin, urlsplit
from lxml import html

from doxagent.content_enrichment.quality import inspect_html, choose_candidate
from doxagent.monitoring.media_enrichment import MediaExtractionResult
from .fintech_body import pdf_text
from .securities_sources import TZERO_API


def portable_html(blocks):
    parts = []
    for block in blocks or []:
        kind = block.get("_type")
        if kind == "block":
            content = "".join(str(c.get("text", "")) for c in block.get("children", []))
            tag = block.get("style") if block.get("style") in {"h1", "h2", "h3", "h4"} else "p"
            parts.append("<" + tag + ">" + escape(content) + "</" + tag + ">")
        elif kind == "table":
            parts.append(
                "<table>"
                + "".join(
                    "<tr>"
                    + "".join("<td>" + escape(str(c)) + "</td>" for c in r.get("cells", []))
                    + "</tr>"
                    for r in block.get("rows", [])
                )
                + "</table>"
            )
        elif kind == "image":
            if block.get("caption"):
                parts.append("<p>" + escape(block["caption"]) + "</p>")
        else:
            raise ValueError("tzero_unsupported_body_block")
    return "".join(parts)


async def tzero_body(http, spec, job, record):
    identity = job.message.raw_payload.get("publisher_article_id")
    if not identity or not spec.accepts(record.url):
        raise ValueError("tzero_article_identity_missing")
    query = '*[_type == "news" && _id == $id && !(_id in path("drafts.**"))][0]{_id,_type,title,slug,publishedAt,body}'
    url = (
        TZERO_API
        + "?"
        + urlencode({"query": query, "$id": json.dumps(identity), "perspective": "published"})
    )
    c = await http.fetch(url, replace(spec, domain="jpd7ydtd.api.sanity.io"))
    if c.status != 200:
        return MediaExtractionResult(
            record=record,
            reason="tzero_cms_http_" + str(c.status),
            diagnostics={"outcome": "UNAVAILABLE"},
        )
    row = json.loads(c.text).get("result")
    if (
        not row
        or row.get("_id") != identity
        or row.get("_type") != "news"
        or row["_id"].startswith("drafts.")
    ):
        raise ValueError("tzero_cms_identity_mismatch")
    slug = row["slug"]["current"] if isinstance(row["slug"], dict) else row["slug"]
    if urlsplit(record.url).path.rstrip("/") != "/news/" + slug:
        raise ValueError("tzero_cms_slug_mismatch")
    try:
        body = portable_html(row.get("body"))
    except ValueError as e:
        return MediaExtractionResult(
            record=record, reason=str(e), diagnostics={"outcome": "PARTIAL"}
        )
    raw = (
        '<html><head><link rel="canonical" href="'
        + escape(record.url, quote=True)
        + '"><meta property="og:type" content="article"></head><body><article><h1>'
        + escape(row["title"])
        + "</h1>"
        + body
        + "</article></body></html>"
    )
    info = await asyncio.to_thread(inspect_html, raw, record.url, job.message.title)
    candidate, outcome, reason = choose_candidate(info, job.message.title)
    return MediaExtractionResult(
        record=record,
        content=candidate.text if candidate else None,
        final_url=record.url,
        http_status=200,
        extraction_method="tzero_published_cms",
        reason=None if candidate else reason,
        diagnostics={
            "outcome": outcome,
            "body_source": "publisher_public_sanity",
            "publisher_article_id": identity,
        },
    )


async def documents_body(http, spec, job, record):
    c = await http.fetch(record.url, spec)
    if c.status != 200:
        return MediaExtractionResult(
            record=record,
            http_status=c.status,
            reason="securities_document_http_" + str(c.status),
            diagnostics={"outcome": "UNAVAILABLE"},
        )
    d = html.fromstring(c.text)
    links = list(job.message.raw_payload.get("attachment_urls", []))
    # Only the actual article's public PDF resources, never footer/site links.
    links.extend(urljoin(record.url, a) for a in d.xpath('//main//a[contains(@href,".pdf")]/@href'))
    links = list(dict.fromkeys(links))
    if any(not spec.accepts(u) for u in links):
        raise ValueError("securities_document_host_not_approved")
    info = await asyncio.to_thread(inspect_html, c.text, record.url, job.message.title)
    candidate, outcome, reason = choose_candidate(info, job.message.title)
    if not links:
        return MediaExtractionResult(
            record=record,
            content=candidate.text if candidate else None,
            final_url=c.url,
            http_status=200,
            extraction_method="securities_public_html",
            reason=None if candidate else reason,
            diagnostics={"outcome": outcome},
        )
    slots = asyncio.Semaphore(2)

    async def document(url):
        async with slots:
            value = await http.fetch(url, spec, binary=True)
            if value.status != 200:
                raise ValueError("securities_attachment_http_" + str(value.status))
            if value.raw_bytes and value.raw_bytes.startswith(b"%PDF-"):
                content = await asyncio.to_thread(pdf_text, value.raw_bytes)
            else:
                detail = await asyncio.to_thread(inspect_html, value.text, url, job.message.title)
                item, quality, _ = choose_candidate(detail, detail.headline or job.message.title)
                if not item or quality not in {"FULL", "SHORT_FULL"}:
                    raise ValueError("securities_attachment_incomplete")
                content = item.text
            return {
                "url": url,
                "sha256": sha256((value.raw_bytes or value.text.encode())).hexdigest(),
                "chars": len(content),
            }, content

    results = await asyncio.gather(*(document(u) for u in links), return_exceptions=True)
    failures = [type(r).__name__ + ":" + str(r) for r in results if isinstance(r, Exception)]
    if failures:
        return MediaExtractionResult(
            record=record,
            reason=failures[0],
            diagnostics={"outcome": "PARTIAL", "attachment_errors": failures},
        )
    intro = candidate.text if candidate else job.message.summary or ""
    if spec.source_id == "sifma_advocacy_press":
        intro = re.split(r"\b(?:More Content|Details Download)\b", intro, maxsplit=1)[0].strip()
    combined = "\n\n".join(
        [
            job.message.title,
            intro,
            *[f"Document: {receipt['url']}\n{content}" for receipt, content in results],
        ]
    )
    return MediaExtractionResult(
        record=record,
        content=combined,
        final_url=c.url,
        http_status=200,
        extraction_method="securities_public_documents",
        diagnostics={
            "outcome": "FULL",
            "attachments": [r[0] for r in results],
            "body_source": "official_html_and_complete_documents",
        },
    )
