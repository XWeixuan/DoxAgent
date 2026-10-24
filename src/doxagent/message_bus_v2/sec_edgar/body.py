"""Full filing BODY on the existing leased enrichment/result pipeline."""

import asyncio
import re
from dataclasses import asdict

import httpx

from doxagent.monitoring.media_enrichment import FetchAttempt, MediaExtractionResult

from .adapter import filing_root, filing_title
from .documents import (
    clean_main,
    document_manifest,
    extract_document,
    extract_schedule_13d,
    merge_documents,
)
from .http import SecHttp, SecHttpError


class SecBodyReader:
    def __init__(self, http=None):
        self.http = http or SecHttp("body")

    async def close(self):
        await self.http.close()

    async def extract(self, job, record):
        filing = job.message.raw_payload["sec_filing"]
        base = filing_root(filing["cik"], filing["accession"])
        primary = filing["primary_document"]
        if record.url != base + primary:
            raise ValueError("sec_filing_identity_mismatch")
        attempts, parts, manifest, skipped, errors = [], [], [], [], []
        total_bytes = 0
        failure_status = None
        try:
            index_url = base + filing["accession"] + "-index.html"
            status, _, index = await self.http.get(index_url, max_bytes=2 * 1024 * 1024)
            attempts.append(FetchAttempt(url=index_url, phase="direct", status_code=status))
            manifest, skipped = document_manifest(index, base, primary, filing["form"])
            # Serial within one filing bounds memory; other filings and ticker
            # HEAD requests stay independent, on their own discovery lane.
            for doc in manifest:
                try:
                    status, _, raw = await self.http.get(doc.url)
                    total_bytes += len(raw)
                    if total_bytes > 48 * 1024 * 1024:
                        raise ValueError("sec_filing_size_limit")
                    if doc.name == primary and doc.name.endswith(".xml"):
                        text = await asyncio.to_thread(
                            extract_schedule_13d, raw, expected_cik=filing["cik"]
                        )
                    else:
                        text = await asyncio.to_thread(extract_document, raw, doc.name)
                    if doc.name == primary:
                        text = clean_main(text, filing["form"])
                    if not text.strip():
                        raise ValueError("sec_document_no_visible_text")
                    parts.append((doc, text))
                    attempts.append(FetchAttempt(url=doc.url, phase="direct", status_code=status))
                except (ValueError, SecHttpError, httpx.HTTPError, TimeoutError) as exc:
                    failure_status = getattr(exc, "status", None)
                    attempts.append(
                        FetchAttempt(
                            url=doc.url,
                            phase="direct",
                            status_code=getattr(exc, "status", None),
                            reason=str(exc),
                            retry_after_seconds=getattr(exc, "retry_seconds", None),
                        )
                    )
                    errors.append({"name": doc.name, "reason": str(exc) or type(exc).__name__})
                    break  # retry this bounded filing, never call a partial bundle FULL
        except (ValueError, SecHttpError, httpx.HTTPError, TimeoutError) as exc:
            failure_status = getattr(exc, "status", None)
            errors.append({"name": "filing_index", "reason": str(exc) or type(exc).__name__})
            attempts.append(
                FetchAttempt(
                    url=filing.get("index_url", record.url),
                    phase="direct",
                    status_code=getattr(exc, "status", None),
                    reason=str(exc),
                    retry_after_seconds=getattr(exc, "retry_seconds", None),
                )
            )
        body, removed = merge_documents(parts)
        if len(body) > 2_000_000:
            errors.append({"name": "bundle", "reason": "sec_assembled_text_limit"})
        if not body:
            errors.append({"name": "bundle", "reason": "sec_empty_filing_body"})
        title = filing_title(filing["company"], filing["form"], filing["selected_items"])
        for doc, text in parts:
            if not doc.kind.startswith("EX-99"):
                continue
            headline = next(
                (
                    line
                    for line in text.splitlines()[:30]
                    if 25 <= len(line) <= 220
                    and " | " not in line
                    and re.search(
                        r"\b(announces?|provides?|enters?|signs?|reports?|"
                        r"launches?|completes?|raises?|prices?|appoints?|"
                        r"closes?|secures?|amends?|agrees?)\b",
                        line,
                        re.I,
                    )
                ),
                None,
            )
            if headline:
                title = f"{filing['form']} — {headline}"
                break
        reason = ("sec_filing_incomplete:" + errors[0]["reason"]) if errors else None
        return MediaExtractionResult(
            record=record,
            content=body or None,
            final_url=record.url,
            reason=reason,
            http_status=failure_status if errors else 200,
            extraction_method="sec_official_filing_bundle",
            attempts=tuple(attempts),
            diagnostics={
                "outcome": "PARTIAL" if errors else "FULL" if len(body) >= 800 else "SHORT_FULL",
                "pipeline_version": "sec_filing_v1",
                "body_source": "sec_official_documents",
                "generated_title": title,
                "sec_filing": filing,
                "documents": [asdict(d) for d in manifest],
                "documents_extracted": len(parts),
                "documents_skipped": skipped,
                "document_errors": errors,
                "bytes_downloaded": total_bytes,
                "exact_duplicate_paragraphs_removed": removed,
            },
        )
