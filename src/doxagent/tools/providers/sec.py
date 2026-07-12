"""SEC EDGAR provider tools with compact, paged company-fact output."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

import httpx

from doxagent.tools.providers.base import (
    DEFAULT_USER_AGENT,
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
    _json_object,
    _normalize_cik,
    _object_list,
)
from doxagent.tools.schema import ToolRequest, ToolResult

SEC_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_MATERIAL_FORMS = frozenset(
    {
        "10-K",
        "10-K/A",
        "10-Q",
        "10-Q/A",
        "8-K",
        "8-K/A",
        "20-F",
        "20-F/A",
        "40-F",
        "6-K",
        "S-1",
        "S-1/A",
        "S-3",
        "S-3/A",
        "DEF 14A",
    }
)
SEC_KEY_FACT_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "ResearchAndDevelopmentExpense",
    "EarningsPerShareDiluted",
    "CommonStockSharesOutstanding",
    "LongTermDebtNoncurrent",
)


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
            if not submissions.get("name") and not submissions.get("filings"):
                return self._failure(
                    request,
                    code="empty_result",
                    message="SEC submissions response contained no company or filing data.",
                    details={"cik": cik},
                )
            include_facts = bool(request.input.get("include_facts", True))
            companyfacts: JsonObject | None = None
            facts_error: Exception | None = None
            if include_facts:
                try:
                    companyfacts = self._get_json(
                        f"{self.settings.sec_data_base_url.rstrip('/')}/api/xbrl/companyfacts/CIK{cik}.json",
                        headers=headers,
                        cache_ttl=self.settings.sec_cache_ttl_seconds,
                        rate_limit_key="sec",
                        min_interval_seconds=self.settings.sec_min_request_interval_seconds,
                        max_rate_limit_retries=1,
                    )
                except (ProviderHttpError, httpx.RequestError) as exc:
                    facts_error = exc

            facts_view = _build_sec_fact_view(companyfacts or {})
            output: JsonObject = {
                "provider": "sec",
                "cik": cik,
                "company": _summarize_sec_company(submissions),
                "recent_filings": _summarize_sec_filings(submissions),
                **facts_view,
            }
            if not include_facts:
                output["facts_status"] = "skipped"
            elif facts_error is not None:
                output["facts_status"] = "unavailable"
                output["facts_error"] = _sec_error_payload(facts_error)
            elif facts_view["fact_directory"]["concept_count"]:
                output["facts_status"] = "available"
            else:
                output["facts_status"] = "empty"

            source_id = f"sec:company:{cik}"
            metadata = {"cik": cik, "include_facts": include_facts}
            if facts_error is not None or (include_facts and output["facts_status"] == "empty"):
                message = (
                    "SEC submissions were retrieved, but companyfacts was unavailable."
                    if facts_error is not None
                    else "SEC submissions were retrieved, but companyfacts contained no concepts."
                )
                return self._partial(
                    request,
                    output=output,
                    raw={"submissions": submissions, "companyfacts": companyfacts},
                    source_kind="external_report",
                    source_id=source_id,
                    title=f"SEC filings and company facts - {request.ticker}",
                    summary=message,
                    source_scope="sec_company_facts_and_filings",
                    confidence=0.72,
                    metadata=metadata,
                    code="sec_companyfacts_unavailable",
                    message=message,
                    retryable=facts_error is not None,
                    details={"facts_error": output.get("facts_error")},
                )
            return self._success(
                request,
                output=output,
                raw={"submissions": submissions, "companyfacts": companyfacts},
                source_kind="external_report",
                source_id=source_id,
                title=f"SEC filings and company facts - {request.ticker}",
                summary="Retrieved material SEC filings and a paged view of key XBRL facts.",
                source_scope="sec_company_facts_and_filings",
                confidence=0.9,
                metadata=metadata,
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
            primary_document = _input_str(request, "primary_document", "")
            if not accession:
                accession, primary_document = self._latest_filing_for_form(request, cik)
            elif not primary_document:
                primary_document = self._primary_document_for_accession(cik, accession)
            clean_accession = accession.replace("-", "")
            # Complete-submission text is a valid last resort when primaryDocument is absent.
            primary_document = primary_document or f"{accession}.txt"
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
            requested_sections = (
                _input_list(request, "sections")
                or _input_list(request, "items")
                or ["Item 1", "Item 1A", "Item 2", "Item 7", "Item 7A", "Item 8", "Item 9A"]
            )
            parsed = parse_sec_sections(text, requested_sections)
            output = {
                "provider": "sec",
                "cik": cik,
                "accession": accession,
                "primary_document": primary_document,
                "source_url": archive_url,
                "sections": parsed["sections"],
                "unknowns": parsed["unknowns"],
            }
            metadata = {
                "cik": cik,
                "accession": accession,
                "primary_document": primary_document,
                "source_url": archive_url,
                "section_count": len(parsed["sections"]),
            }
            source_id = f"sec:filing:{cik}:{accession}"
            if not parsed["sections"]:
                return self._partial(
                    request,
                    output=output,
                    raw=None,
                    source_kind="external_report",
                    source_id=source_id,
                    title=f"SEC filing sections - {request.ticker}",
                    summary=(
                        "SEC filing was retrieved, but none of the requested sections "
                        "were found."
                    ),
                    source_scope="sec_filing_sections",
                    confidence=0.25,
                    metadata=metadata,
                    code="sec_sections_not_found",
                    message="None of the requested SEC filing sections were found.",
                    details={"unknowns": parsed["unknowns"]},
                )
            if parsed["unknowns"]:
                return self._partial(
                    request,
                    output=output,
                    raw=None,
                    source_kind="external_report",
                    source_id=source_id,
                    title=f"SEC filing sections - {request.ticker}",
                    summary="Retrieved only some requested SEC filing sections.",
                    source_scope="sec_filing_sections",
                    confidence=0.72,
                    metadata=metadata,
                    code="sec_partial_sections",
                    message="Some requested SEC filing sections were not found.",
                    details={"unknowns": parsed["unknowns"]},
                )
            return self._success(
                request,
                output=output,
                raw=None,
                source_kind="external_report",
                source_id=source_id,
                title=f"SEC filing sections - {request.ticker}",
                summary="Parsed requested SEC filing sections from the original filing text.",
                source_scope="sec_filing_sections",
                confidence=0.82,
                metadata=metadata,
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _latest_filing_for_form(self, request: ToolRequest, cik: str) -> tuple[str, str]:
        form = _input_str(request, "form", "10-K")
        recent = self._recent_filings(cik)
        forms = _object_list(recent.get("form"))
        accessions = _object_list(recent.get("accessionNumber"))
        primary_documents = _object_list(recent.get("primaryDocument"))
        for index, raw_form in enumerate(forms):
            if str(raw_form).upper() == form.upper() and index < len(accessions):
                document = str(primary_documents[index]) if index < len(primary_documents) else ""
                return str(accessions[index]), document
        raise ValueError(f"No recent SEC filing found for form {form} and CIK {cik}.")

    def _primary_document_for_accession(self, cik: str, accession: str) -> str:
        recent = self._recent_filings(cik)
        accessions = _object_list(recent.get("accessionNumber"))
        primary_documents = _object_list(recent.get("primaryDocument"))
        for index, item in enumerate(accessions):
            if str(item) == accession and index < len(primary_documents):
                return str(primary_documents[index])
        return ""

    def _recent_filings(self, cik: str) -> JsonObject:
        submissions = self._get_json(
            f"{self.settings.sec_data_base_url.rstrip('/')}/submissions/CIK{cik}.json",
            headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
            cache_ttl=self.settings.sec_cache_ttl_seconds,
            rate_limit_key="sec",
            min_interval_seconds=self.settings.sec_min_request_interval_seconds,
            max_rate_limit_retries=1,
        )
        return _json_object(_json_object(submissions.get("filings", {})).get("recent", {}))


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
    for index, (section, start, heading_end, heading) in enumerate(matches):
        end = matches[index + 1][1] if index + 1 < len(matches) else min(len(text), start + 25_000)
        sections.append(
            {
                "section": section,
                "heading_match": heading,
                "start_offset": start,
                "end_offset": end,
                "text": text[heading_end:end].strip()[:20_000],
            }
        )
    found = {str(item["section"]) for item in sections}
    unknowns = [
        {"field": section, "reason": "section heading not found"}
        for section in target_sections
        if section not in found
    ]
    return {"sections": sections, "unknowns": unknowns}


def _strip_html(raw_text: str) -> str:
    no_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_text)
    no_tags = re.sub(r"(?s)<[^>]+>", " ", no_scripts)
    return re.sub(r"\s+", " ", html.unescape(no_tags))


def _summarize_sec_company(submissions: Mapping[str, Any]) -> JsonObject:
    return {
        "name": submissions.get("name"),
        "tickers": deepcopy(submissions.get("tickers", [])),
        "exchanges": deepcopy(submissions.get("exchanges", [])),
        "sic": submissions.get("sic"),
        "sic_description": submissions.get("sicDescription"),
        "entity_type": submissions.get("entityType"),
        "fiscal_year_end": submissions.get("fiscalYearEnd"),
    }


def _summarize_sec_filings(submissions: Mapping[str, Any], limit: int = 20) -> list[JsonObject]:
    recent = _json_object(_json_object(submissions.get("filings", {})).get("recent", {}))
    forms = _object_list(recent.get("form"))
    accessions = _object_list(recent.get("accessionNumber"))
    filing_dates = _object_list(recent.get("filingDate"))
    report_dates = _object_list(recent.get("reportDate"))
    primary_docs = _object_list(recent.get("primaryDocument"))
    items: list[JsonObject] = []
    for index, raw_form in enumerate(forms):
        form = str(raw_form).upper()
        if form not in SEC_MATERIAL_FORMS:
            continue
        items.append(
            {
                "form": raw_form,
                "accession": accessions[index] if index < len(accessions) else None,
                "filing_date": filing_dates[index] if index < len(filing_dates) else None,
                "report_date": report_dates[index] if index < len(report_dates) else None,
                "primary_document": primary_docs[index] if index < len(primary_docs) else None,
            }
        )
        if len(items) >= limit:
            break
    return items


def _build_sec_fact_view(companyfacts: JsonObject) -> JsonObject:
    facts = companyfacts.get("facts")
    concepts: list[tuple[str, str, JsonObject]] = []
    if isinstance(facts, dict):
        for taxonomy in sorted(facts):
            taxonomy_facts = facts[taxonomy]
            if not isinstance(taxonomy_facts, dict):
                continue
            for concept in sorted(taxonomy_facts):
                definition = taxonomy_facts[concept]
                if isinstance(definition, dict):
                    concepts.append((str(taxonomy), str(concept), definition))
    key_facts = [
        _fact_preview(
            taxonomy,
            concept,
            definition,
            observation_limit=2,
            include_description=False,
        )
        for taxonomy, concept, definition in concepts
        if taxonomy == "us-gaap" and concept in SEC_KEY_FACT_CONCEPTS
    ]
    pages: JsonObject = {}
    for index, (taxonomy, concept, definition) in enumerate(concepts, start=1):
        page_id = f"page_{index:04d}"
        pages[page_id] = _fact_preview(
            taxonomy,
            concept,
            definition,
            observation_limit=1,
            include_description=False,
        )
    return {
        "key_facts": key_facts,
        "fact_directory": {
            "concept_count": len(concepts),
            "page_count": len(pages),
            "concepts_per_page": 1,
            "page_ref_template": "obs_<tool_call_id>::/fact_pages/page_####",
            "first_page_key": "page_0001" if pages else None,
            "taxonomies": sorted({taxonomy for taxonomy, _, _ in concepts}),
            "note": (
                "Each page keeps a concept and its latest exact provider observations; "
                "full history remains task-local raw data."
            ),
        },
        "fact_pages": pages,
    }


def _fact_preview(
    taxonomy: str,
    concept: str,
    definition: JsonObject,
    *,
    observation_limit: int,
    include_description: bool = True,
) -> JsonObject:
    observations: list[JsonObject] = []
    units = definition.get("units")
    available_units: list[str] = []
    if isinstance(units, dict):
        available_units = sorted(str(unit) for unit in units)
        candidates: list[tuple[str, JsonObject]] = []
        for unit, rows in units.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    candidates.append((str(unit), row))
        candidates.sort(key=lambda item: _fact_sort_key(item[1]), reverse=True)
        seen: set[tuple[object, ...]] = set()
        for unit, row in candidates:
            identity = (
                unit,
                row.get("accn"),
                row.get("end"),
                row.get("form"),
                row.get("fp"),
                row.get("val"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            observations.append({"unit": unit, "observation": deepcopy(row)})
            if len(observations) >= observation_limit:
                break
    preview = {
        "taxonomy": taxonomy,
        "concept": concept,
        "label": definition.get("label"),
        "available_units": available_units,
        "latest_observations": observations,
    }
    if include_description:
        preview["description"] = definition.get("description")
    return preview


def _fact_sort_key(row: JsonObject) -> tuple[str, str, str]:
    return (
        str(row.get("filed") or ""),
        str(row.get("end") or ""),
        str(row.get("start") or ""),
    )


def _sec_error_payload(exc: Exception) -> JsonObject:
    if isinstance(exc, ProviderHttpError):
        return {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
            "details": exc.details,
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "code": "upstream_unavailable",
            "message": str(exc) or repr(exc),
            "retryable": True,
            "details": {"provider_error": type(exc).__name__},
        }
    return {
        "code": "tool_execution_failed",
        "message": str(exc) or repr(exc),
        "retryable": False,
        "details": {"provider_error": type(exc).__name__},
    }
