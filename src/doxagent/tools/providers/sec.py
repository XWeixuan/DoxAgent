"""SEC EDGAR provider tools with compact, paged company-fact output."""

from __future__ import annotations

import html
import re
import ssl
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from doxagent.models import ResultStatus
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
    "CostOfRevenue",
    "GrossProfit",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "ResearchAndDevelopmentExpense",
    "EarningsPerShareDiluted",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "CommonStockSharesOutstanding",
    "InventoryNet",
    "AccountsReceivableNetCurrent",
    "AccountsPayableCurrent",
    "MarketableSecuritiesCurrent",
    "LongTermDebtNoncurrent",
)
SEC_C1_CANONICAL_METRICS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    "cost_of_revenue": ("CostOfRevenue",),
    "gross_profit": ("GrossProfit",),
    "net_income": ("NetIncomeLoss",),
    "operating_income": ("OperatingIncomeLoss",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditure": (
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "diluted_weighted_average_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "shares_outstanding": ("CommonStockSharesOutstanding",),
    "inventory": ("InventoryNet",),
    "accounts_receivable": ("AccountsReceivableNetCurrent",),
    "accounts_payable": ("AccountsPayableCurrent",),
    "marketable_securities_current": ("MarketableSecuritiesCurrent",),
    "long_term_debt": ("LongTermDebtNoncurrent",),
}

_SEC_CONCEPT_FALLBACKS = {
    concept: (metric, candidates)
    for metric, candidates in SEC_C1_CANONICAL_METRICS.items()
    for concept in candidates
}

_SEC_DEFAULT_SECTIONS = {
    "10-Q": (
        "Financial Statements",
        "Management's Discussion and Analysis",
        "Quantitative and Qualitative Disclosures About Market Risk",
        "Controls and Procedures",
        "Risk Factors",
    ),
    "10-K": ("Item 1", "Item 1A", "Item 7", "Item 7A", "Item 8", "Item 9A"),
    "20-F": ("Item 3D", "Item 5", "Item 8", "Item 15"),
    "40-F": ("Management's Discussion and Analysis", "Financial Statements"),
    "8-K": ("Item 1.01", "Item 2.02", "Item 8.01", "Item 9.01"),
}


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

            cutoff_date = str(request.metadata.get("cutoff_at") or "")[:10]
            facts_view = _build_sec_fact_view(companyfacts or {}, cutoff_date=cutoff_date)
            principal_filing_date = _latest_principal_filing_date(
                submissions,
                cutoff_date=cutoff_date,
            )
            _annotate_fact_freshness(facts_view.get("key_facts"), principal_filing_date)
            fact_items = facts_view.get("key_facts")
            stale_concepts = [
                str(item.get("concept"))
                for item in (fact_items if isinstance(fact_items, list) else [])
                if isinstance(item, dict) and item.get("stale_for_principal_cycle") is True
            ]
            output: JsonObject = {
                "provider": "sec",
                "cik": cik,
                "company": _summarize_sec_company(submissions),
                "recent_filings": _summarize_sec_filings(submissions),
                "latest_principal_filing_date": principal_filing_date,
                "published_at": principal_filing_date,
                "as_of": _latest_fact_period(fact_items),
                "applied_cutoff": cutoff_date or None,
                "stale_concepts": stale_concepts,
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
            if (
                facts_error is not None
                or (include_facts and output["facts_status"] == "empty")
                or stale_concepts
            ):
                message = (
                    "SEC submissions were retrieved, but companyfacts was unavailable."
                    if facts_error is not None
                    else (
                        "SEC submissions were retrieved, but companyfacts contained no concepts."
                        if output["facts_status"] == "empty"
                        else "SEC submissions were retrieved with stale canonical company facts."
                    )
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
                    code=(
                        "sec_companyfacts_stale"
                        if stale_concepts and facts_error is None
                        else "sec_companyfacts_unavailable"
                    ),
                    message=message,
                    retryable=facts_error is not None,
                    details={
                        "facts_error": output.get("facts_error"),
                        "stale_concepts": stale_concepts,
                    },
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

    def _filing_exhibit_inventory(self, cik: str, accession: str) -> list[JsonObject]:
        index_url = _sec_filing_index_url(cik, accession)
        index_html = self._get_text(
            index_url,
            headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
            cache_ttl=self.settings.sec_cache_ttl_seconds,
            rate_limit_key="sec",
            min_interval_seconds=self.settings.sec_min_request_interval_seconds,
            max_rate_limit_retries=1,
        )
        return parse_sec_filing_index(index_html, index_url)


class SecInsiderTransactionsEnrichedClient(SecCompanyFactsAndFilingsClient):
    """Read bounded issuer Form 4 XML with ownership and footnote context."""

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
            recent = _json_object(_json_object(submissions.get("filings", {})).get("recent", {}))
            forms = _object_list(recent.get("form"))
            accessions = _object_list(recent.get("accessionNumber"))
            documents = _object_list(recent.get("primaryDocument"))
            filing_dates = _object_list(recent.get("filingDate"))
            cutoff_date = str(request.metadata.get("cutoff_at") or "")[:10]
            limit = max(1, min(25, int(request.input.get("limit", 10))))
            records: list[JsonObject] = []
            failures: list[JsonObject] = []
            for index, form in enumerate(forms):
                if str(form).upper() not in {"4", "4/A"} or index >= len(accessions):
                    continue
                filing_date = str(filing_dates[index]) if index < len(filing_dates) else ""
                if cutoff_date and filing_date and filing_date > cutoff_date:
                    continue
                accession = str(accessions[index])
                primary_document = str(documents[index]) if index < len(documents) else ""
                if not primary_document:
                    failures.append({"accession": accession, "code": "primary_document_missing"})
                    continue
                # SEC submissions may expose the presentation transform prefix
                # (for example ``xslF345X06/``).  The archive stores the raw
                # ownership XML at the basename; requesting the transform path
                # can hang or 404 and is not the canonical source document.
                primary_document = primary_document.rsplit("/", maxsplit=1)[-1]
                source_url = (
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{accession.replace('-', '')}/{primary_document}"
                )
                try:
                    raw_xml = self._get_text(
                        source_url,
                        headers=headers,
                        cache_ttl=self.settings.sec_cache_ttl_seconds,
                        rate_limit_key="sec",
                        min_interval_seconds=self.settings.sec_min_request_interval_seconds,
                        max_rate_limit_retries=1,
                    )
                    parsed = _parse_form4_xml(raw_xml)
                except Exception as exc:
                    failures.append(
                        {
                            "accession": accession,
                            "code": type(exc).__name__,
                            "message": str(exc)[:300],
                        }
                    )
                    continue
                records.append(
                    {
                        "form": str(form).upper(),
                        "filing_date": filing_date or None,
                        "accession": accession,
                        "source_url": source_url,
                        **parsed,
                    }
                )
                if len(records) >= limit:
                    break
            if not records:
                return self._failure(
                    request,
                    code="empty_result",
                    message="No usable issuer Form 4 XML was available before the cutoff.",
                    details={"cik": cik, "failures": failures},
                )
            output = {
                "provider": "sec",
                "cik": cik,
                "ticker": request.ticker,
                "applied_cutoff": cutoff_date or None,
                "filings": records,
                "failed_filings": failures,
                "transaction_code_legend": {
                    "P": "open-market or private purchase",
                    "S": "open-market or private sale",
                    "A": "grant, award, or other acquisition",
                    "D": "disposition to issuer or other disposition",
                    "F": "tax or exercise-price payment by delivery/withholding",
                    "M": "exercise or conversion of derivative security",
                    "G": "gift",
                },
                "published_at": records[0].get("filing_date"),
                "as_of": records[0].get("filing_date"),
            }
            kwargs = dict(
                output=output,
                raw=None,
                source_kind="external_report",
                source_id=f"sec:form4:{cik}",
                title=f"SEC enriched Form 4 transactions - {request.ticker}",
                summary=f"Retrieved and parsed {len(records)} issuer Form 4 filing(s).",
                source_scope="sec_insider_transactions_enriched",
                confidence=0.92,
                metadata={"cik": cik, "filing_count": len(records)},
            )
            if failures:
                return self._partial(
                    request,
                    code="sec_partial_form4",
                    message="Some Form 4 filings could not be parsed.",
                    retryable=False,
                    details={"failures": failures},
                    **kwargs,
                )
            return self._success(request, **kwargs)
        except Exception as exc:
            return self._handle_exception(request, exc)


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
            metadata_error: JsonObject | None = None
            try:
                filing_metadata = self._filing_metadata(cik, accession)
            except Exception as exc:
                # The filing body remains usable when SEC submissions metadata is
                # temporarily unavailable. Preserve the gap instead of failing the
                # deterministic section extraction.
                filing_metadata = {}
                metadata_error = _sec_error_payload(exc)
            form = _input_str(request, "form", "") or str(filing_metadata.get("form") or "10-K")
            form = form.upper()
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
            default_form = form.removesuffix("/A")
            requested_sections = (
                _input_list(request, "sections")
                or _input_list(request, "items")
                or list(_SEC_DEFAULT_SECTIONS.get(default_form, _SEC_DEFAULT_SECTIONS["10-K"]))
            )
            parsed = parse_sec_sections(text, requested_sections)
            section_previews = [
                {key: value for key, value in section.items() if key != "text"}
                | {"text_preview": str(section.get("text") or "")[:3_000]}
                for section in parsed["sections"]
            ]
            output = {
                "provider": "sec",
                "cik": cik,
                "form": form,
                "accession": accession,
                "primary_document": primary_document,
                "source_url": archive_url,
                "filing_date": filing_metadata.get("filing_date"),
                "report_date": filing_metadata.get("report_date"),
                "published_at": filing_metadata.get("filing_date"),
                "as_of": filing_metadata.get("report_date"),
                "applied_cutoff": str(request.metadata.get("cutoff_at") or "")[:10] or None,
                "default_sections_for_form": not (
                    _input_list(request, "sections") or _input_list(request, "items")
                ),
                "section_previews": section_previews,
                "sections": parsed["sections"],
                "unknowns": parsed["unknowns"],
                "filing_metadata_error": metadata_error,
            }
            metadata = {
                "cik": cik,
                "form": form,
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
                        "SEC filing was retrieved, but none of the requested sections were found."
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
        filing_dates = _object_list(recent.get("filingDate"))
        cutoff_date = str(request.metadata.get("cutoff_at") or "")[:10]
        for index, raw_form in enumerate(forms):
            if str(raw_form).upper() == form.upper() and index < len(accessions):
                if cutoff_date and index < len(filing_dates):
                    if str(filing_dates[index]) > cutoff_date:
                        continue
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

    def _filing_metadata(self, cik: str, accession: str) -> JsonObject:
        recent = self._recent_filings(cik)
        accessions = _object_list(recent.get("accessionNumber"))
        for index, item in enumerate(accessions):
            if str(item) != accession:
                continue
            return {
                key: (str(values[index]) if index < len(values) and values[index] else None)
                for key, values in {
                    "form": _object_list(recent.get("form")),
                    "filing_date": _object_list(recent.get("filingDate")),
                    "report_date": _object_list(recent.get("reportDate")),
                }.items()
            }
        return {}

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

    def _call_recent_matching(self, request: ToolRequest, *, max_filings: int = 12) -> ToolResult:
        """Search bounded recent filings when requested Items are alternatives."""
        if _input_str(request, "accession", ""):
            return SecFilingSectionsClient.call(self, request)
        cik = self._resolve_cik(request)
        form = _input_str(request, "form", "8-K").upper()
        recent = self._recent_filings(cik)
        forms = _object_list(recent.get("form"))
        accessions = _object_list(recent.get("accessionNumber"))
        documents = _object_list(recent.get("primaryDocument"))
        attempted = 0
        last_result: ToolResult | None = None
        for index, raw_form in enumerate(forms):
            if str(raw_form).upper() != form or index >= len(accessions):
                continue
            candidate = ToolRequest.model_validate(
                {
                    **request.model_dump(),
                    "input": {
                        **request.input,
                        "accession": str(accessions[index]),
                        "primary_document": (
                            str(documents[index]) if index < len(documents) else ""
                        ),
                    },
                }
            )
            last_result = SecFilingSectionsClient.call(self, candidate)
            attempted += 1
            sections = last_result.output.get("sections") if last_result.output else None
            if isinstance(sections, list) and sections:
                last_result.output["searched_recent_filings"] = attempted
                return last_result
            if attempted >= max_filings:
                break
        if last_result is not None:
            last_result.output["searched_recent_filings"] = attempted
            return last_result
        return SecFilingSectionsClient.call(self, request)


# Semantic SEC clients deliberately retain the two legacy clients above.  The
# factory may therefore migrate callers one tool at a time without changing the
# wire contract of ``sec.company_facts_and_filings`` or ``sec.filing_sections``.
class SecIssuerFilingsClient(SecCompanyFactsAndFilingsClient):
    """Return a governed filing index; it does not fetch filing bodies."""

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            cik = self._resolve_cik(request)
            submissions = self._get_json(
                f"{self.settings.sec_data_base_url.rstrip('/')}/submissions/CIK{cik}.json",
                headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
                cache_ttl=self.settings.sec_cache_ttl_seconds,
                rate_limit_key="sec",
                min_interval_seconds=self.settings.sec_min_request_interval_seconds,
            )
            forms = {item.upper() for item in _input_list(request, "forms")}
            limit = int(request.input.get("limit", 50))
            filings = _all_sec_filings(submissions, limit=500)
            if forms:
                filings = [item for item in filings if str(item.get("form", "")).upper() in forms]
            cutoff_date = str(request.metadata.get("cutoff_at") or "")[:10]
            if cutoff_date:
                filings = [
                    item
                    for item in filings
                    if not item.get("filing_date") or str(item["filing_date"]) <= cutoff_date
                ]
            limit_per_form = bool(request.input.get("limit_per_form", len(forms) > 1))
            if forms and limit_per_form:
                per_form: dict[str, int] = {form: 0 for form in forms}
                selected_filings: list[JsonObject] = []
                for filing in filings:
                    form = str(filing.get("form") or "").upper()
                    if form not in per_form or per_form[form] >= limit:
                        continue
                    selected_filings.append(filing)
                    per_form[form] += 1
                filings = selected_filings
            else:
                filings = filings[:limit]
            include_exhibits = bool(request.input.get("include_exhibits", limit <= 10))
            inventory_errors: list[JsonObject] = []
            for index, filing in enumerate(filings):
                accession = str(filing.get("accession") or "")
                if not accession:
                    continue
                filing["filing_index_url"] = _sec_filing_index_url(cik, accession)
                if not include_exhibits or index >= 10:
                    continue
                try:
                    filing["exhibits"] = self._filing_exhibit_inventory(cik, accession)
                except Exception as exc:
                    inventory_errors.append(
                        {"accession": accession, "error": _sec_error_payload(exc)}
                    )
            if not filings:
                return self._partial(
                    request,
                    output={
                        "provider": "sec",
                        "cik": cik,
                        "company": _summarize_sec_company(submissions),
                        "filings": [],
                        "applied_cutoff": cutoff_date or None,
                    },
                    raw=submissions,
                    source_kind="external_report",
                    source_id=f"sec:issuer:{cik}",
                    title=f"SEC issuer filing index - {request.ticker}",
                    summary=(
                        "SEC issuer index was retrieved, but no filing matched "
                        "the requested filter."
                    ),
                    source_scope="sec_issuer_filings",
                    confidence=0.75,
                    metadata={"cik": cik, "form_filter": sorted(forms)},
                    code="empty_result",
                    message="No SEC filing matched the requested filter.",
                )
            return self._success(
                request,
                output={
                    "provider": "sec",
                    "cik": cik,
                    "company": _summarize_sec_company(submissions),
                    "filings": filings,
                    "published_at": filings[0].get("filing_date"),
                    "as_of": filings[0].get("report_date"),
                    "applied_cutoff": cutoff_date or None,
                    "exhibit_inventory_errors": inventory_errors,
                    "limit_semantics": "per_form" if forms and limit_per_form else "total",
                },
                raw=submissions,
                source_kind="external_report",
                source_id=f"sec:issuer:{cik}",
                title=f"SEC issuer filing index - {request.ticker}",
                summary="Retrieved a filtered SEC submission index with filing-index coordinates.",
                source_scope="sec_issuer_filings",
                confidence=0.94,
                metadata={"cik": cik, "form_filter": sorted(forms)},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class SecCompanyFinancialsClient(SecCompanyFactsAndFilingsClient):
    """Return selected XBRL concepts and their exact reported observations."""

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            cik = self._resolve_cik(request)
            facts = self._get_json(
                f"{self.settings.sec_data_base_url.rstrip('/')}/api/xbrl/companyfacts/CIK{cik}.json",
                headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
                cache_ttl=self.settings.sec_cache_ttl_seconds,
                rate_limit_key="sec",
                min_interval_seconds=self.settings.sec_min_request_interval_seconds,
            )
            requested = set(_input_list(request, "concepts"))
            cutoff_date = str(request.metadata.get("cutoff_at") or "")[:10]
            view = _build_sec_fact_view(facts, cutoff_date=cutoff_date)
            concept_resolution: list[JsonObject] = []
            if requested:
                resolved, concept_resolution = _resolve_requested_sec_concepts(
                    facts, requested, cutoff_date=cutoff_date
                )
                previews = _requested_sec_fact_previews(facts, resolved, cutoff_date=cutoff_date)
                for preview in previews:
                    concept = str(preview.get("concept") or "")
                    preview["requested_via"] = [
                        str(item["requested_concept"])
                        for item in concept_resolution
                        if item.get("resolved_concept") == concept
                    ]
                matched_concepts = sorted(
                    {
                        str(item.get("requested_concept"))
                        for item in concept_resolution
                        if item.get("resolution") in {"exact", "fallback"}
                    }
                )
                view = {
                    "key_facts": previews,
                    "requested_concepts": sorted(requested),
                    "matched_concepts": matched_concepts,
                    "unmatched_concepts": sorted(requested - set(matched_concepts)),
                    "concept_resolution": concept_resolution,
                }
            submissions = self._get_json(
                f"{self.settings.sec_data_base_url.rstrip('/')}/submissions/CIK{cik}.json",
                headers={"User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT},
                cache_ttl=self.settings.sec_cache_ttl_seconds,
                rate_limit_key="sec",
                min_interval_seconds=self.settings.sec_min_request_interval_seconds,
            )
            principal_filing_date = _latest_principal_filing_date(
                submissions,
                cutoff_date=cutoff_date,
            )
            _annotate_fact_freshness(view.get("key_facts"), principal_filing_date)
            key_facts = view.get("key_facts")
            fact_items = key_facts if isinstance(key_facts, list) else []
            stale_concepts = [
                str(item.get("concept"))
                for item in fact_items
                if isinstance(item, dict) and item.get("stale_for_principal_cycle") is True
            ]
            unmatched = view.get("unmatched_concepts")
            output = {
                "provider": "sec",
                "cik": cik,
                "latest_principal_filing_date": principal_filing_date,
                "published_at": principal_filing_date,
                "as_of": _latest_fact_period(key_facts),
                "applied_cutoff": cutoff_date or None,
                "stale_concepts": stale_concepts,
                **view,
            }
            common = {
                "output": output,
                "raw": facts,
                "source_kind": "external_report",
                "source_id": f"sec:companyfacts:{cik}",
                "title": f"SEC XBRL company facts - {request.ticker}",
                "source_scope": "sec_company_financials",
                "metadata": {"cik": cik, "concept_count": len(view["key_facts"])},
            }
            if (isinstance(unmatched, list) and unmatched) or stale_concepts:
                return self._partial(
                    request,
                    **common,
                    summary=("Retrieved SEC XBRL facts with explicit missing or stale concepts."),
                    confidence=0.78,
                    code="sec_financials_incomplete",
                    message="Some requested or canonical SEC concepts are missing or stale.",
                    details={
                        "unmatched_concepts": unmatched or [],
                        "stale_concepts": stale_concepts,
                    },
                )
            return self._success(
                request,
                **common,
                summary="Retrieved governed SEC XBRL financial facts.",
                confidence=0.93,
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class SecFilingContentClient(SecFilingSectionsClient):
    """Semantic alias for original filing content and section coordinates."""

    def call(self, request: ToolRequest) -> ToolResult:
        result = super().call(request)
        if result.output:
            result.output["source_coordinates"]["source_scope"] = "sec_filing_content"
        return result


class SecMaterialContractsProjectsClient(SecFilingSectionsClient):
    def call(self, request: ToolRequest) -> ToolResult:
        copied = ToolRequest.model_validate(
            {
                **request.model_dump(),
                "input": {
                    **request.input,
                    "form": request.input.get("form", "8-K"),
                    "sections": request.input.get(
                        "sections", ["Item 1.01", "Item 2.03", "Item 2.04"]
                    ),
                },
            }
        )
        result = self._call_recent_matching(copied)
        if result.output:
            result.output["record_type"] = "material_contract_or_project_disclosure"
            result.output["source_coordinates"]["source_scope"] = "sec_material_contracts_projects"
            valid, rejected = _validate_material_sections(result.output.get("sections"))
            result.output["sections"] = valid
            result.output["rejected_semantic_matches"] = rejected
            result.output["section_previews"] = [
                {key: value for key, value in section.items() if key != "text"}
                | {"text_preview": str(section.get("text") or "")[:3_000]}
                for section in valid
            ]
            if not valid:
                return result.model_copy(
                    update={
                        "status": ResultStatus.EMPTY,
                        "error": None,
                        "output_summary": (
                            "SEC filing text contained no material contract or quantified "
                            "commitment disclosure that passed semantic validation."
                        ),
                    }
                )
        return _accept_any_sec_section(result)


class SecManagementDisclosuresClient(SecFilingSectionsClient):
    def call(self, request: ToolRequest) -> ToolResult:
        requested_form = _input_str(request, "form", "8-K").upper()
        base_form = requested_form.removesuffix("/A")
        if base_form in {"10-Q", "10-K", "20-F", "40-F"}:
            defaults = (
                ["Management's Discussion and Analysis"]
                if base_form in {"10-Q", "40-F"}
                else ["Item 7"]
            )
            copied = ToolRequest.model_validate(
                {
                    **request.model_dump(),
                    "input": {
                        **request.input,
                        "form": requested_form,
                        "sections": request.input.get("sections") or defaults,
                    },
                }
            )
            result = self._call_recent_matching(copied)
            if result.output:
                result.output["record_type"] = "management_discussion_and_analysis"
                result.output["source_coordinates"]["source_scope"] = "sec_management_disclosures"
            return result

        copied = ToolRequest.model_validate(
            {
                **request.model_dump(),
                "input": {
                    **request.input,
                    "form": requested_form,
                    "sections": request.input.get("sections") or ["Item 2.02"],
                },
            }
        )
        if not _input_str(copied, "accession", ""):
            cik = self._resolve_cik(copied)
            recent = self._recent_filings(cik)
            forms = _object_list(recent.get("form"))
            accessions = _object_list(recent.get("accessionNumber"))
            documents = _object_list(recent.get("primaryDocument"))
            filing_items = _object_list(recent.get("items"))
            cutoff_date = str(copied.metadata.get("cutoff_at") or "")[:10]
            filing_dates = _object_list(recent.get("filingDate"))
            for index, raw_form in enumerate(forms):
                if str(raw_form).upper() != str(copied.input["form"]).upper():
                    continue
                if cutoff_date and index < len(filing_dates):
                    if str(filing_dates[index]) > cutoff_date:
                        continue
                items = str(filing_items[index]) if index < len(filing_items) else ""
                if "2.02" not in {item.strip() for item in items.split(",")}:
                    continue
                copied = copied.model_copy(
                    update={
                        "input": {
                            **copied.input,
                            "accession": str(accessions[index]),
                            "primary_document": (
                                str(documents[index]) if index < len(documents) else ""
                            ),
                        }
                    }
                )
                break
        result = SecFilingSectionsClient.call(self, copied)
        if result.output:
            result.output["record_type"] = "management_disclosure"
            result.output["source_coordinates"]["source_scope"] = "sec_management_disclosures"
            cik = str(result.output.get("cik") or "")
            accession = str(result.output.get("accession") or "")
            if cik and accession:
                try:
                    inventory = self._filing_exhibit_inventory(cik, accession)
                    exhibits = [
                        exhibit
                        for exhibit in inventory
                        if str(exhibit.get("type") or "").upper() in {"EX-99", "EX-99.1", "EX-99.2"}
                    ]
                    result.output["exhibits"] = exhibits
                    documents: list[JsonObject] = []
                    for exhibit in exhibits:
                        exhibit_type = str(exhibit.get("type") or "").upper()
                        if exhibit_type not in {"EX-99", "EX-99.1", "EX-99.2"}:
                            continue
                        source_url = str(exhibit.get("url") or "")
                        if not source_url:
                            continue
                        raw_text = self._get_text(
                            source_url,
                            headers={
                                "User-Agent": self.settings.sec_user_agent or DEFAULT_USER_AGENT
                            },
                            cache_ttl=self.settings.sec_cache_ttl_seconds,
                            rate_limit_key="sec",
                            min_interval_seconds=self.settings.sec_min_request_interval_seconds,
                            max_rate_limit_retries=1,
                        )
                        cleaned = _strip_html(raw_text)
                        documents.append(
                            {
                                **exhibit,
                                "text": cleaned[:60_000],
                                "full_text_chars": len(cleaned),
                                "truncated": len(cleaned) > 60_000,
                            }
                        )
                    result.output["exhibit_documents"] = documents
                    result.output["exhibit_previews"] = [
                        {key: value for key, value in document.items() if key != "text"}
                        | {"text_preview": str(document.get("text") or "")[:3_000]}
                        for document in documents
                    ]
                    if documents:
                        return result.model_copy(
                            update={
                                "status": ResultStatus.SUCCEEDED,
                                "error": None,
                                "output_summary": (
                                    "Retrieved the earnings 8-K and its EX-99 management "
                                    "disclosure documents."
                                ),
                            }
                        )
                except Exception as exc:
                    result.output["exhibit_inventory_error"] = _sec_error_payload(exc)
        return result


def parse_sec_sections(raw_text: str, target_sections: Iterable[str]) -> JsonObject:
    text = _normalize_sec_text(_strip_html(raw_text))
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for section in target_sections:
        escaped = re.escape(section).replace(r"\ ", r"\s+")
        patterns.append((section, re.compile(rf"\b{escaped}\b[\.\s:-]*", re.I)))
    matches: list[tuple[str, int, int, str, int]] = []
    for section, pattern in patterns:
        candidates: list[tuple[int, int, re.Match[str]]] = []
        for match in pattern.finditer(text):
            section_key = _section_key(section)
            named_heading = (
                not section_key.startswith("item ") and section_key != "financial statements"
            )
            normalized_match = match.group(0).strip().rstrip(".:- ")
            if (
                named_heading
                and normalized_match.casefold() != section.strip().rstrip(".:- ").casefold()
            ):
                continue
            end = _sec_section_end(text, section, match.end())
            body = text[match.end() : end].strip()
            score = min(len(body), 80_000)
            before = text[max(0, match.start() - 2_000) : match.start()].lower()
            immediate_before = text[max(0, match.start() - 90) : match.start()].lower()
            after = text[match.end() : min(len(text), match.end() + 240)]
            after_toc_window = text[match.end() : min(len(text), match.end() + 1_000)]
            if "table of contents" in before and re.search(
                r"\b\d{1,3}\s+Item\s+\d", after_toc_window, re.I
            ):
                score -= 100_000
            if re.search(
                r"(?:refer(?:red)?\s+to|set\s+forth\s+in|described\s+(?:under|in)|"
                r"included\s+in|see)\b",
                immediate_before,
            ):
                score -= 100_000
            if re.search(r"part\s+[ivx]+,?\s*$", immediate_before):
                score -= 100_000
            if re.search(r"\b\d{1,3}\s+Item\s+", after, re.I):
                score -= 50_000
            expected_title = _SEC_SECTION_TITLES.get(_section_key(section))
            if expected_title and expected_title.search(after):
                score += 40_000
            if section_key == "financial statements" and re.search(
                r"\bItem\s+1\b[.\s:-]*$", immediate_before, re.I
            ):
                score += 40_000
            named_item = _SEC_NAMED_ITEM_PREFIXES.get(section_key)
            if named_item and named_item.search(immediate_before):
                # Named 10-Q headings frequently reappear in cross-references and
                # quoted prose. Prefer the actual Item heading over a longer body
                # that merely contains the title.
                score += 100_000
            if match.group(0).strip() == section:
                score += 2_000
            candidates.append((score, end, match))
        if candidates:
            _score, end, match = max(candidates, key=lambda item: (item[0], item[2].start()))
            body = text[match.end() : end].strip()
            if len(body) >= 30:
                matches.append((section, match.start(), match.end(), match.group(0).strip(), end))
    matches.sort(key=lambda item: item[1])
    sections: list[JsonObject] = []
    for section, start, heading_end, heading, end in matches:
        body = text[heading_end:end].strip()
        sections.append(
            {
                "section": section,
                "heading_match": heading,
                "start_offset": start,
                "end_offset": end,
                "text": body[:40_000],
                "full_text_chars": len(body),
                "returned_chars": min(len(body), 40_000),
                "truncated": len(body) > 40_000,
                "match_quality": "substantive_body",
            }
        )
    found = {str(item["section"]) for item in sections}
    unknowns = [
        {"field": section, "reason": "section heading not found"}
        for section in target_sections
        if section not in found
    ]
    return {"sections": sections, "unknowns": unknowns}


_SEC_SECTION_TITLES = {
    "financial statements": re.compile(r"financial\s+statements", re.I),
    "management's discussion and analysis": re.compile(
        r"management.{0,30}discussion.{0,20}analysis", re.I
    ),
    "quantitative and qualitative disclosures about market risk": re.compile(
        r"quantitative.{0,40}qualitative.{0,50}market\s+risk", re.I
    ),
    "controls and procedures": re.compile(r"controls\s+and\s+procedures", re.I),
    "risk factors": re.compile(r"risk\s+factors", re.I),
    "item 1a": re.compile(r"risk\s+factors", re.I),
    "item 2": re.compile(r"management.{0,20}discussion|unregistered\s+sales", re.I),
    "item 2.02": re.compile(r"results\s+of\s+operations|financial\s+condition", re.I),
    "item 7": re.compile(r"management.{0,20}discussion", re.I),
    "item 7a": re.compile(r"quantitative.{0,30}qualitative", re.I),
    "item 8": re.compile(r"financial\s+statements", re.I),
}

_SEC_NAMED_ITEM_PREFIXES = {
    "financial statements": re.compile(r"\bItem\s+1\b[.\s:-]*$", re.I),
    "management's discussion and analysis": re.compile(r"\bItem\s+2\b[.\s:-]*$", re.I),
    "quantitative and qualitative disclosures about market risk": re.compile(
        r"\bItem\s+3\b[.\s:-]*$", re.I
    ),
    "controls and procedures": re.compile(r"\bItem\s+4\b[.\s:-]*$", re.I),
    "risk factors": re.compile(r"\bItem\s+1A\b[.\s:-]*$", re.I),
}

_SEC_SECTION_BOUNDARIES = {
    "financial statements": re.compile(
        r"\bItem\s+2\b[.\s:-]*(?:Management.{0,40}Discussion)", re.I
    ),
    "item 1a": re.compile(
        r"\bItem\s+(?:1B|2)\b[.\s:-]*(?:Unresolved|Properties|Unregistered)", re.I
    ),
    "item 2": re.compile(r"\bItem\s+(?:3|5)\b[.\s:-]*(?:Quantitative|Other\s+Information)", re.I),
    "item 2.02": re.compile(r"\bItem\s+9\.01\b", re.I),
    "item 7": re.compile(r"\bItem\s+(?:7A|8)\b", re.I),
    "item 7a": re.compile(r"\bItem\s+8\b", re.I),
    "item 8": re.compile(r"\bItem\s+9\b", re.I),
    "management's discussion and analysis": re.compile(
        r"\bItem\s+3\b[.\s:-]*(?:Quantitative|Market\s+Risk)", re.I
    ),
    "quantitative and qualitative disclosures about market risk": re.compile(
        r"\bItem\s+4\b[.\s:-]*(?:Controls|Procedures)", re.I
    ),
    "controls and procedures": re.compile(r"\bPart\s+II\b|\bItem\s+1\b", re.I),
    "risk factors": re.compile(
        r"\bItem\s+(?:1B|2)\b[.\s:-]*(?:Unresolved|Unregistered|Properties)", re.I
    ),
}


def _section_key(section: str) -> str:
    return re.sub(r"\s+", " ", section.strip().lower()).rstrip(".:")


def _sec_section_end(text: str, section: str, start: int) -> int:
    boundary = _SEC_SECTION_BOUNDARIES.get(_section_key(section))
    if boundary is not None:
        match = boundary.search(text, start)
        if match:
            return match.start()
    return min(len(text), start + 80_000)


def parse_sec_filing_index(index_html: str, index_url: str) -> list[JsonObject]:
    """Parse the SEC filing-index document table into bounded exhibit coordinates."""

    exhibits: list[JsonObject] = []
    for row_html in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", index_html):
        cells = re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", row_html)
        if len(cells) < 4:
            continue
        description = _strip_html(cells[1]).strip()
        document_cell = cells[2]
        href_match = re.search(r"(?is)href\s*=\s*[\"']([^\"']+)", document_cell)
        document = _strip_html(document_cell).strip().split(" ", 1)[0]
        exhibit_type = _strip_html(cells[3]).strip()
        if not exhibit_type.upper().startswith("EX-"):
            continue
        href = href_match.group(1) if href_match else document
        exhibits.append(
            {
                "sequence": _strip_html(cells[0]).strip() or None,
                "type": exhibit_type,
                "description": description or None,
                "filename": urlparse(href).path.rsplit("/", 1)[-1] or document,
                "url": urljoin(index_url, href),
                "size_bytes": (_strip_html(cells[4]).strip() if len(cells) > 4 else None),
            }
        )
    return exhibits


def _sec_filing_index_url(cik: str, accession: str) -> str:
    clean_accession = accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{clean_accession}/{accession}-index.html"
    )


def _accept_any_sec_section(result: ToolResult) -> ToolResult:
    sections = result.output.get("sections") if result.output else None
    if (
        result.status is ResultStatus.PARTIAL
        and result.error is not None
        and result.error.code == "sec_partial_sections"
        and isinstance(sections, list)
        and sections
    ):
        return result.model_copy(
            update={
                "status": ResultStatus.SUCCEEDED,
                "error": None,
                "output_summary": (
                    "Retrieved at least one requested alternative SEC disclosure section."
                ),
            }
        )
    return result


def _validate_material_sections(value: object) -> tuple[list[JsonObject], list[JsonObject]]:
    if not isinstance(value, list):
        return [], []
    valid: list[JsonObject] = []
    rejected: list[JsonObject] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section") or "")
        text = str(raw.get("text") or "")
        normalized = text.lower()
        has_amount = bool(
            re.search(
                r"(?:\$|usd\s*)\s*\d|\d[\d,.]*\s*(?:million|billion|thousand)",
                normalized,
            )
        )
        has_date_or_term = bool(
            re.search(
                r"\b(?:20\d{2}|effective\s+date|maturity|expires?|term(?:ination)?|years?)\b",
                normalized,
            )
        )
        has_obligation = any(
            token in normalized
            for token in (
                "agreement",
                "commitment",
                "obligation",
                "purchase",
                "borrow",
                "credit facility",
                "counterparty",
                "contract",
            )
        )
        item = _section_key(section)
        passes = (
            item in {"item 1.01", "item 2.03", "item 2.04"}
            and has_obligation
            and (has_amount or has_date_or_term)
        ) or (item not in {"item 1.01", "item 2.03", "item 2.04"} and has_amount and has_obligation)
        if passes:
            raw["semantic_validation"] = {
                "has_amount": has_amount,
                "has_date_or_term": has_date_or_term,
                "has_obligation_language": has_obligation,
            }
            valid.append(raw)
        else:
            rejected.append(
                {
                    "section": section,
                    "reason": "matched text lacked verifiable contract or commitment anchors",
                    "has_amount": has_amount,
                    "has_date_or_term": has_date_or_term,
                    "has_obligation_language": has_obligation,
                }
            )
    return valid, rejected


def _strip_html(raw_text: str) -> str:
    no_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_text)
    no_tags = re.sub(r"(?s)<[^>]+>", " ", no_scripts)
    return re.sub(r"\s+", " ", html.unescape(no_tags))


def _parse_form4_xml(raw_xml: str) -> JsonObject:
    xml_match = re.search(r"(?is)<ownershipDocument\b.*?</ownershipDocument>", raw_xml)
    payload = xml_match.group(0) if xml_match else raw_xml
    root = ET.fromstring(payload)

    def text(path: str, node: ET.Element = root) -> str | None:
        found = node.find(path)
        if found is None or found.text is None:
            return None
        value = found.text.strip()
        return value or None

    owner = root.find("reportingOwner")
    relationship = owner.find("reportingOwnerRelationship") if owner is not None else None
    reporting_owner = {
        "cik": text("reportingOwnerId/rptOwnerCik", owner) if owner is not None else None,
        "name": text("reportingOwnerId/rptOwnerName", owner) if owner is not None else None,
        "is_director": text("isDirector", relationship) if relationship is not None else None,
        "is_officer": text("isOfficer", relationship) if relationship is not None else None,
        "is_ten_percent_owner": text("isTenPercentOwner", relationship)
        if relationship is not None
        else None,
        "officer_title": text("officerTitle", relationship) if relationship is not None else None,
    }
    reporting_owner = {key: value for key, value in reporting_owner.items() if value is not None}
    footnotes = {
        str(item.attrib.get("id")): (item.text or "").strip()
        for item in root.findall("footnotes/footnote")
        if item.attrib.get("id") and (item.text or "").strip()
    }
    transactions: list[JsonObject] = []
    for security_type, path in (
        ("non_derivative", "nonDerivativeTable/nonDerivativeTransaction"),
        ("derivative", "derivativeTable/derivativeTransaction"),
    ):
        for transaction in root.findall(path):
            footnote_ids = [
                str(item.attrib.get("id"))
                for item in transaction.findall(".//footnoteId")
                if item.attrib.get("id")
            ]
            transactions.append(
                {
                    "security_type": security_type,
                    "security_title": text("securityTitle/value", transaction),
                    "transaction_date": text("transactionDate/value", transaction),
                    "transaction_code": text("transactionCoding/transactionCode", transaction),
                    "equity_swap_involved": text(
                        "transactionCoding/equitySwapInvolved", transaction
                    ),
                    "shares": _sec_number(
                        text("transactionAmounts/transactionShares/value", transaction)
                    ),
                    "price_per_share": _sec_number(
                        text("transactionAmounts/transactionPricePerShare/value", transaction)
                    ),
                    "acquired_or_disposed": text(
                        "transactionAmounts/transactionAcquiredDisposedCode/value", transaction
                    ),
                    "shares_owned_after": _sec_number(
                        text(
                            "postTransactionAmounts/sharesOwnedFollowingTransaction/value",
                            transaction,
                        )
                    ),
                    "direct_or_indirect": text(
                        "ownershipNature/directOrIndirectOwnership/value", transaction
                    ),
                    "nature_of_ownership": text(
                        "ownershipNature/natureOfOwnership/value", transaction
                    ),
                    "exercise_date": text("exerciseDate/value", transaction),
                    "expiration_date": text("expirationDate/value", transaction),
                    "underlying_security_title": text(
                        "underlyingSecurity/underlyingSecurityTitle/value", transaction
                    ),
                    "underlying_shares": _sec_number(
                        text("underlyingSecurity/underlyingSecurityShares/value", transaction)
                    ),
                    "footnote_ids": footnote_ids,
                }
            )
    cleaned = [
        {key: value for key, value in item.items() if value not in (None, "", [])}
        for item in transactions
    ]
    return {
        "period_of_report": text("periodOfReport"),
        "reporting_owner": reporting_owner,
        "transactions": cleaned,
        "footnotes": footnotes,
        "transaction_count": len(cleaned),
    }


def _sec_number(value: object) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _normalize_sec_text(value: str) -> str:
    """Normalize Unicode punctuation without changing character offsets."""

    return value.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2013": "-",
                "\u2014": "-",
                "\u00a0": " ",
            }
        )
    )


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


def _all_sec_filings(submissions: Mapping[str, Any], limit: int = 50) -> list[JsonObject]:
    """Preserve the filing index semantic tool's full form coverage."""
    recent = _json_object(_json_object(submissions.get("filings", {})).get("recent", {}))
    forms = _object_list(recent.get("form"))
    accessions = _object_list(recent.get("accessionNumber"))
    filing_dates = _object_list(recent.get("filingDate"))
    report_dates = _object_list(recent.get("reportDate"))
    primary_docs = _object_list(recent.get("primaryDocument"))
    return [
        {
            "form": form,
            "accession": accessions[index] if index < len(accessions) else None,
            "filing_date": filing_dates[index] if index < len(filing_dates) else None,
            "report_date": report_dates[index] if index < len(report_dates) else None,
            "primary_document": primary_docs[index] if index < len(primary_docs) else None,
        }
        for index, form in enumerate(forms[:limit])
    ]


def _build_sec_fact_view(companyfacts: JsonObject, *, cutoff_date: str = "") -> JsonObject:
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
    raw_key_facts = [
        _fact_preview(
            taxonomy,
            concept,
            definition,
            observation_limit=2,
            include_description=False,
            cutoff_date=cutoff_date,
        )
        for taxonomy, concept, definition in concepts
        if taxonomy == "us-gaap" and concept in SEC_KEY_FACT_CONCEPTS
    ]
    key_facts = _canonical_sec_key_facts(raw_key_facts)
    pages: JsonObject = {}
    for index, (taxonomy, concept, definition) in enumerate(concepts, start=1):
        page_id = f"page_{index:04d}"
        pages[page_id] = _fact_preview(
            taxonomy,
            concept,
            definition,
            observation_limit=1,
            include_description=False,
            cutoff_date=cutoff_date,
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


def _requested_sec_fact_previews(
    companyfacts: JsonObject, requested: set[str], *, cutoff_date: str = ""
) -> list[JsonObject]:
    facts = companyfacts.get("facts")
    previews: list[JsonObject] = []
    if not isinstance(facts, dict):
        return previews
    for taxonomy, taxonomy_facts in facts.items():
        if not isinstance(taxonomy_facts, dict):
            continue
        for concept in sorted(requested):
            definition = taxonomy_facts.get(concept)
            if isinstance(definition, dict):
                previews.append(
                    _fact_preview(
                        str(taxonomy),
                        concept,
                        definition,
                        observation_limit=8,
                        include_description=False,
                        cutoff_date=cutoff_date,
                    )
                )
    return previews


def _resolve_requested_sec_concepts(
    companyfacts: JsonObject, requested: set[str], *, cutoff_date: str = ""
) -> tuple[set[str], list[JsonObject]]:
    facts = companyfacts.get("facts")
    available: set[str] = set()
    if isinstance(facts, dict):
        for taxonomy_facts in facts.values():
            if isinstance(taxonomy_facts, dict):
                available.update(str(concept) for concept in taxonomy_facts)
    resolved: set[str] = set()
    resolution: list[JsonObject] = []
    for concept in sorted(requested):
        metric_and_candidates = _SEC_CONCEPT_FALLBACKS.get(concept)
        metric = metric_and_candidates[0] if metric_and_candidates else None
        candidates = metric_and_candidates[1] if metric_and_candidates else (concept,)
        governed_choice = max(
            (
                candidate
                for candidate in candidates
                if candidate in available
                and (
                    not cutoff_date
                    or _concept_latest_filed(companyfacts, candidate, cutoff_date=cutoff_date)
                )
            ),
            key=lambda candidate: (
                _concept_latest_filed(companyfacts, candidate, cutoff_date=cutoff_date),
                candidate == concept,
            ),
            default=None,
        )
        if governed_choice:
            resolved.add(governed_choice)
            exact = governed_choice == concept
            resolution.append(
                {
                    "requested_concept": concept,
                    "resolved_concept": governed_choice,
                    "canonical_metric": metric,
                    "resolution": "exact" if exact else "fallback",
                    **(
                        {}
                        if exact
                        else {
                            "reason": ("used the freshest governed issuer concept for this metric")
                        }
                    ),
                }
            )
            continue
        if concept in available:
            resolved.add(concept)
            resolution.append(
                {
                    "requested_concept": concept,
                    "resolved_concept": concept,
                    "canonical_metric": metric,
                    "resolution": "exact",
                }
            )
        else:
            resolution.append(
                {
                    "requested_concept": concept,
                    "resolved_concept": None,
                    "canonical_metric": metric,
                    "resolution": "unmatched",
                }
            )
    return resolved, resolution


def _concept_latest_filed(companyfacts: JsonObject, concept: str, *, cutoff_date: str = "") -> str:
    facts = companyfacts.get("facts")
    if not isinstance(facts, dict):
        return ""
    dates: list[str] = []
    for taxonomy_facts in facts.values():
        if not isinstance(taxonomy_facts, dict):
            continue
        definition = taxonomy_facts.get(concept)
        if not isinstance(definition, dict):
            continue
        units = definition.get("units")
        if not isinstance(units, dict):
            continue
        for rows in units.values():
            if isinstance(rows, list):
                dates.extend(
                    str(row.get("filed"))
                    for row in rows
                    if isinstance(row, dict)
                    and row.get("filed")
                    and (not cutoff_date or str(row["filed"]) <= cutoff_date)
                )
    return max(dates, default="")


def _latest_fact_period(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    periods: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        observations = item.get("latest_observations")
        if not isinstance(observations, list):
            continue
        for wrapped in observations:
            if not isinstance(wrapped, dict):
                continue
            observation = wrapped.get("observation")
            if isinstance(observation, dict) and observation.get("end"):
                periods.append(str(observation["end"]))
    return max(periods, default=None)


def _fact_preview(
    taxonomy: str,
    concept: str,
    definition: JsonObject,
    *,
    observation_limit: int,
    include_description: bool = True,
    cutoff_date: str = "",
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
                if isinstance(row, dict) and (
                    not cutoff_date or not row.get("filed") or str(row["filed"]) <= cutoff_date
                ):
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
        "latest_filed": max(
            (
                str(item["observation"].get("filed") or "")
                for item in observations
                if isinstance(item.get("observation"), dict)
            ),
            default=None,
        ),
    }
    if include_description:
        preview["description"] = definition.get("description")
    return preview


def _canonical_sec_key_facts(previews: list[JsonObject]) -> list[JsonObject]:
    by_concept = {str(item.get("concept")): item for item in previews}
    selected: list[JsonObject] = []
    used: set[str] = set()
    for metric, concepts in SEC_C1_CANONICAL_METRICS.items():
        candidates = [by_concept[concept] for concept in concepts if concept in by_concept]
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda item: (
                str(item.get("latest_filed") or ""),
                -concepts.index(str(item.get("concept"))),
            ),
        )
        best["canonical_metric"] = metric
        best["canonical_selection"] = True
        selected.append(best)
        used.update(concepts)
    selected.extend(item for item in previews if str(item.get("concept")) not in used)
    return selected


def _latest_principal_filing_date(
    submissions: Mapping[str, Any], *, cutoff_date: str = ""
) -> str | None:
    recent = _json_object(_json_object(submissions.get("filings", {})).get("recent", {}))
    forms = _object_list(recent.get("form"))
    filing_dates = _object_list(recent.get("filingDate"))
    candidates = [
        str(filing_dates[index])
        for index, form in enumerate(forms)
        if str(form).upper() in {"10-K", "10-Q", "20-F", "40-F"}
        and index < len(filing_dates)
        and (not cutoff_date or str(filing_dates[index]) <= cutoff_date)
    ]
    return max(candidates, default=None)


def _annotate_fact_freshness(value: object, principal_filing_date: str | None) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, dict):
            continue
        latest_filed = str(item.get("latest_filed") or "")
        stale: bool | None = None
        if latest_filed and principal_filing_date:
            try:
                stale = (
                    date.fromisoformat(principal_filing_date) - date.fromisoformat(latest_filed)
                ).days > 120
            except ValueError:
                stale = None
        item["stale_for_principal_cycle"] = stale


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
    if isinstance(exc, ssl.SSLError):
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
