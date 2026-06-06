"""SEC EDGAR provider tools."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from typing import Any

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import (
    DEFAULT_USER_AGENT,
    BaseRealToolClient,
    JsonObject,
    _input_list,
    _input_str,
    _json_object,
    _normalize_cik,
    _object_list,
)
from doxagent.tools.schema import ToolRequest, ToolResult

SEC_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"

class SecCompanyFactsAndFilingsClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            cik = self._resolve_cik(request)
            headers = {"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT}
            submissions = self._get_json(
                f"{self.settings.sec_data_base_url.rstrip('/')}/submissions/CIK{cik}.json",
                headers=headers,
                cache_ttl=self.settings.sec_cache_ttl_seconds,
                rate_limit_key="sec",
                min_interval_seconds=self.settings.sec_min_request_interval_seconds,
                max_rate_limit_retries=1,
            )
            include_facts = bool(request.input.get("include_facts", True))
            companyfacts: JsonObject | None = None
            if include_facts:
                companyfacts = self._get_json(
                    f"{self.settings.sec_data_base_url.rstrip('/')}/api/xbrl/companyfacts/CIK{cik}.json",
                    headers=headers,
                    cache_ttl=self.settings.sec_cache_ttl_seconds,
                    rate_limit_key="sec",
                    min_interval_seconds=self.settings.sec_min_request_interval_seconds,
                    max_rate_limit_retries=1,
                )
            output = {
                "provider": "sec",
                "cik": cik,
                "submissions": _summarize_sec_submissions(submissions),
                "companyfacts": companyfacts or {},
            }
            return self._success(
                request,
                output=output,
                raw={"submissions": submissions, "companyfacts": companyfacts},
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"sec:company:{cik}",
                title=f"SEC filings and companyfacts for {request.ticker}",
                summary="SEC submissions and XBRL company facts were retrieved.",
                citation_scope="sec_company_facts_and_filings",
                confidence=0.9,
                metadata={"cik": cik, "include_facts": include_facts},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _resolve_cik(self, request: ToolRequest) -> str:
        raw_cik = request.input.get("cik")
        if isinstance(raw_cik, str) and raw_cik.strip():
            return _normalize_cik(raw_cik)
        ticker = _input_str(request, "ticker", request.ticker).upper()
        mapping = self._get_json(
            SEC_TICKER_CIK_URL,
            headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
            cache_ttl=self.settings.sec_cache_ttl_seconds,
            rate_limit_key="sec",
            min_interval_seconds=self.settings.sec_min_request_interval_seconds,
            max_rate_limit_retries=1,
        )
        for entry in mapping.values():
            if isinstance(entry, Mapping) and str(entry.get("ticker", "")).upper() == ticker:
                return _normalize_cik(str(entry.get("cik_str", "")))
        raise ValueError(f"SEC CIK not found for ticker {ticker}.")


class SecFilingSectionsClient(SecCompanyFactsAndFilingsClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            cik = self._resolve_cik(request)
            accession = _input_str(request, "accession", "")
            if not accession:
                accession = self._latest_accession_for_form(request, cik)
            clean_accession = accession.replace("-", "")
            primary_document = _input_str(request, "primary_document", "")
            if not primary_document:
                primary_document = f"{clean_accession}.txt"
            archive_url = (
                "https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{clean_accession}/{primary_document}"
            )
            text = self._get_text(
                archive_url,
                headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
                cache_ttl=self.settings.sec_cache_ttl_seconds,
                rate_limit_key="sec",
                min_interval_seconds=self.settings.sec_min_request_interval_seconds,
                max_rate_limit_retries=1,
            )
            sections = _input_list(request, "sections") or [
                "Item 1",
                "Item 1A",
                "Item 2",
                "Item 7",
                "Item 7A",
                "Item 8",
                "Item 9A",
            ]
            parsed = parse_sec_sections(text, sections)
            output = {
                "provider": "sec",
                "cik": cik,
                "accession": accession,
                "primary_document": primary_document,
                "source_url": archive_url,
                "sections": parsed["sections"],
                "unknowns": parsed["unknowns"],
            }
            return self._success(
                request,
                output=output,
                raw=None,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"sec:filing:{cik}:{accession}",
                title=f"SEC filing sections for {request.ticker}",
                summary="SEC filing text sections were parsed deterministically.",
                citation_scope="sec_filing_sections",
                confidence=0.82 if parsed["sections"] else 0.35,
                metadata={
                    "cik": cik,
                    "accession": accession,
                    "primary_document": primary_document,
                    "source_url": archive_url,
                    "section_count": len(parsed["sections"]),
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _latest_accession_for_form(self, request: ToolRequest, cik: str) -> str:
        form = _input_str(request, "form", "10-K")
        submissions = self._get_json(
            f"{self.settings.sec_data_base_url.rstrip('/')}/submissions/CIK{cik}.json",
            headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
            cache_ttl=self.settings.sec_cache_ttl_seconds,
            rate_limit_key="sec",
            min_interval_seconds=self.settings.sec_min_request_interval_seconds,
            max_rate_limit_retries=1,
        )
        recent = _json_object(_json_object(submissions.get("filings", {})).get("recent", {}))
        forms = _object_list(recent.get("form"))
        accessions = _object_list(recent.get("accessionNumber"))
        for index, raw_form in enumerate(forms):
            if str(raw_form).upper() == form.upper() and index < len(accessions):
                return str(accessions[index])
        raise ValueError(f"No recent SEC filing found for form {form} and CIK {cik}.")
def parse_sec_sections(raw_text: str, target_sections: Iterable[str]) -> JsonObject:
    text = _strip_html(raw_text)
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for section in target_sections:
        escaped = re.escape(section).replace(r"\ ", r"\s+")
        patterns.append((section, re.compile(rf"\b{escaped}\b[\.\s:-]*", re.I)))
    matches: list[tuple[str, int, int, str]] = []
    for section, pattern in patterns:
        match = pattern.search(text)
        if match:
            matches.append((section, match.start(), match.end(), match.group(0).strip()))
    matches.sort(key=lambda item: item[1])
    sections: list[JsonObject] = []
    unknowns: list[JsonObject] = []
    for index, (section, start, heading_end, heading) in enumerate(matches):
        end = matches[index + 1][1] if index + 1 < len(matches) else min(len(text), start + 25000)
        body = text[heading_end:end].strip()
        sections.append(
            {
                "section": section,
                "heading_match": heading,
                "start_offset": start,
                "end_offset": end,
                "text": body[:20000],
            }
        )
    found = {item["section"] for item in sections}
    for section in target_sections:
        if section not in found:
            unknowns.append({"field": section, "reason": "section heading not found"})
    return {"sections": sections, "unknowns": unknowns}


def _strip_html(raw_text: str) -> str:
    no_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_text)
    no_tags = re.sub(r"(?s)<[^>]+>", " ", no_scripts)
    decoded = html.unescape(no_tags)
    return re.sub(r"\s+", " ", decoded)
def _summarize_sec_submissions(submissions: Mapping[str, Any]) -> JsonObject:
    recent = _json_object(_json_object(submissions.get("filings", {})).get("recent", {}))
    forms = _object_list(recent.get("form"))
    accessions = _object_list(recent.get("accessionNumber"))
    filing_dates = _object_list(recent.get("filingDate"))
    primary_docs = _object_list(recent.get("primaryDocument"))
    recent_items: list[JsonObject] = []
    for index, raw_form in enumerate(forms[:20]):
        recent_items.append(
            {
                "form": raw_form,
                "accession": accessions[index] if index < len(accessions) else None,
                "filing_date": filing_dates[index] if index < len(filing_dates) else None,
                "primary_document": primary_docs[index] if index < len(primary_docs) else None,
            }
        )
    return {
        "name": submissions.get("name"),
        "tickers": submissions.get("tickers", []),
        "exchanges": submissions.get("exchanges", []),
        "sic": submissions.get("sic"),
        "sic_description": submissions.get("sicDescription"),
        "recent_filings": recent_items,
    }
