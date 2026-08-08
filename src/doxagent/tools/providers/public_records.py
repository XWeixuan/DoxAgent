"""Read-only semantic clients for public procurement, regulation and issuer IR.

These clients intentionally expose records with source coordinates, rather than
turning narrative documents into investment conclusions.  All credentials are
read from settings by the central configuration layer; this module never logs a
credential or places one in ``ToolResult``.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    _input_list,
    _input_str,
    _strip_none,
)
from doxagent.tools.schema import ToolRequest, ToolResult


def _setting(client: BaseRealToolClient, name: str) -> str | None:
    value = getattr(client.settings, name, None)
    return str(value) if value else None


class _PublicJsonClient(BaseRealToolClient):
    provider = "public"
    base_url = ""
    key_setting: str | None = None
    key_param = "api_key"
    cache_setting = "macro_cache_ttl_seconds"

    def _params(self, request: ToolRequest, *, defaults: JsonObject | None = None) -> JsonObject:
        params = dict(defaults or {})
        supplied = request.input.get("params", {})
        if isinstance(supplied, dict):
            params.update(_strip_none(supplied))
        key = _setting(self, self.key_setting) if self.key_setting else None
        if key:
            params.setdefault(self.key_param, key)
        return params

    def _read(
        self,
        request: ToolRequest,
        path: str,
        *,
        params: JsonObject | None = None,
        record_type: str,
        scope: str,
        source_id: str | None = None,
    ) -> ToolResult:
        try:
            raw = self._get_json(
                self.base_url.rstrip("/") + path,
                params=params,
                cache_ttl=int(getattr(self.settings, self.cache_setting, 900)),
                rate_limit_key=self.provider,
                min_interval_seconds=0.1,
                max_rate_limit_retries=1,
            )
            records = _project_public_records(self.provider, record_type, _records(raw))
            output = {
                "provider": self.provider,
                "record_type": record_type,
                "records": records,
                "record_count": len(records),
            }
            if not records:
                return self._partial(
                    request,
                    output=output,
                    raw={"records": records},
                    source_kind="official_public_record",
                    source_id=source_id or f"{self.provider}:{record_type}",
                    title=f"{self.provider} {record_type}",
                    summary="The official endpoint returned no matching public records.",
                    source_scope=scope,
                    confidence=0.6,
                    metadata={"endpoint": path, "query_present": bool(params)},
                    code="empty_result",
                    message="No matching public records were returned.",
                )
            return self._success(
                request,
                output=output,
                raw={"records": records},
                source_kind="official_public_record",
                source_id=source_id or f"{self.provider}:{record_type}",
                title=f"{self.provider} {record_type}",
                summary=f"Retrieved {len(records)} official public records.",
                source_scope=scope,
                confidence=0.9,
                metadata={"endpoint": path, "query_present": bool(params)},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class UsaSpendingAwardSearchClient(_PublicJsonClient):
    provider, base_url = "usaspending", "https://api.usaspending.gov/api/v2"

    def call(self, request: ToolRequest) -> ToolResult:
        filters = request.input.get("filters")
        if not isinstance(filters, dict):
            return self._failure(
                request,
                code="invalid_input",
                message="filters object is required for award search.",
            )
        if not filters.get("award_type_codes"):
            return self._failure(
                request,
                code="invalid_input",
                message="filters.award_type_codes is required by the USAspending award-search API.",
            )
        body = {
            "filters": filters,
            "fields": request.input.get(
                "fields",
                [
                    "Award ID",
                    "generated_internal_id",
                    "Recipient Name",
                    "Award Amount",
                    "Start Date",
                    "End Date",
                ],
            ),
            "page": int(request.input.get("page", 1)),
            "limit": int(request.input.get("limit", 25)),
            "sort": request.input.get("sort", "Award Amount"),
            "order": request.input.get("order", "desc"),
        }
        try:
            raw = self._post_json(
                self.base_url + "/search/spending_by_award/",
                json_body=body,
                cache_ttl=int(getattr(self.settings, "macro_cache_ttl_seconds", 900)),
            )
            records = _project_public_records(self.provider, "award_search", _records(raw))
            return self._success(
                request,
                output={
                    "provider": self.provider,
                    "record_type": "award_search",
                    "records": records,
                    "page_metadata": raw.get("page_metadata", {}),
                },
                raw={"records": records},
                source_kind="official_public_record",
                source_id="usaspending:award_search",
                title="USAspending award search",
                summary=f"Retrieved {len(records)} federal award records.",
                source_scope="usaspending_award_search",
                confidence=0.9,
                metadata={"endpoint": "/search/spending_by_award/", "page": body["page"]},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class UsaSpendingAwardDetailClient(_PublicJsonClient):
    provider, base_url = "usaspending", "https://api.usaspending.gov/api/v2"

    def call(self, request: ToolRequest) -> ToolResult:
        award_id = _input_str(request, "award_id", "")
        if not award_id:
            return self._failure(request, code="invalid_input", message="award_id is required.")
        return self._read(
            request,
            f"/awards/{award_id}/",
            record_type="award_detail",
            scope="usaspending_award_detail",
            source_id=f"usaspending:award:{award_id}",
        )


class SamContractOpportunitiesClient(_PublicJsonClient):
    provider, base_url, key_setting = "sam", "https://api.sam.gov/opportunities/v2", "sam_api_key"

    def call(self, request: ToolRequest) -> ToolResult:
        posted_from, posted_to = (
            _input_str(request, "posted_from", ""),
            _input_str(request, "posted_to", ""),
        )
        if not posted_from or not posted_to:
            return self._failure(
                request,
                code="invalid_input",
                message="posted_from and posted_to are required by SAM.gov.",
            )
        return self._read(
            request,
            "/search",
            params=self._params(
                request,
                defaults={
                    "postedFrom": posted_from,
                    "postedTo": posted_to,
                    "limit": int(request.input.get("limit", 25)),
                },
            ),
            record_type="contract_opportunity",
            scope="sam_contract_opportunities",
        )


class RegulationsRulemakingRecordsClient(_PublicJsonClient):
    provider, base_url, key_setting = (
        "regulations",
        "https://api.regulations.gov/v4",
        "regulations_api_key",
    )

    def call(self, request: ToolRequest) -> ToolResult:
        mode = _input_str(request, "mode", "documents")
        if mode not in {"documents", "dockets"}:
            return self._failure(
                request, code="invalid_input", message="mode must be documents or dockets."
            )
        supplied = request.input.get("params", {})
        page_size = supplied.get("page[size]") if isinstance(supplied, dict) else None
        if page_size is not None and int(page_size) < 5:
            return self._failure(
                request,
                code="invalid_input",
                message="Regulations.gov page[size] must be at least 5.",
            )
        identifier = _input_str(request, "id", "")
        path = f"/{mode}/{identifier}" if identifier else f"/{mode}"
        return self._read(
            request,
            path,
            params=self._params(request),
            record_type=f"rulemaking_{mode}",
            scope="regulations_rulemaking_records",
        )


class FederalRegisterDocumentsClient(_PublicJsonClient):
    provider, base_url = "federal_register", "https://www.federalregister.gov/api/v1"

    def call(self, request: ToolRequest) -> ToolResult:
        document_number = _input_str(request, "document_number", "")
        path = f"/documents/{document_number}.json" if document_number else "/documents.json"
        return self._read(
            request,
            path,
            params=self._params(request),
            record_type="federal_register_document",
            scope="federal_register_documents",
            source_id=f"federal_register:{document_number or 'search'}",
        )


class CongressLegislativeActionsClient(_PublicJsonClient):
    provider, base_url, key_setting = "congress", "https://api.congress.gov/v3", "congress_api_key"

    def call(self, request: ToolRequest) -> ToolResult:
        resource = _input_str(request, "resource", "bill")
        if resource not in {"bill", "committee-report", "hearing"}:
            return self._failure(
                request,
                code="invalid_input",
                message="resource must be bill, committee-report, or hearing.",
            )
        identifier = _input_str(request, "identifier", "")
        path = f"/{resource}/{identifier}" if identifier else f"/{resource}"
        return self._read(
            request,
            path,
            params=self._params(request),
            record_type=f"congress_{resource}",
            scope="congress_legislative_actions",
        )


class OpenFdaApprovalMilestonesClient(_PublicJsonClient):
    provider, base_url, key_setting = "openfda", "https://api.fda.gov", "openfda_api_key"

    def call(self, request: ToolRequest) -> ToolResult:
        dataset = _input_str(request, "dataset", "device/510k")
        if dataset not in {"device/510k", "device/pma", "drug/drugsfda"}:
            return self._failure(
                request,
                code="invalid_input",
                message="dataset must be device/510k, device/pma, or drug/drugsfda.",
            )
        return self._read(
            request,
            f"/{dataset}.json",
            params=self._params(request),
            record_type="approval_milestone",
            scope="openfda_approval_milestones",
        )


class OpenFdaSafetyActionsClient(_PublicJsonClient):
    provider, base_url, key_setting = "openfda", "https://api.fda.gov", "openfda_api_key"

    def call(self, request: ToolRequest) -> ToolResult:
        dataset = _input_str(request, "dataset", "device/enforcement")
        if dataset not in {"device/enforcement", "drug/enforcement", "device/event", "drug/event"}:
            return self._failure(
                request, code="invalid_input", message="unsupported openFDA safety dataset."
            )
        return self._read(
            request,
            f"/{dataset}.json",
            params=self._params(request),
            record_type="safety_action",
            scope="openfda_safety_actions",
        )


class IrOfficialFeedDiscoveryClient(BaseRealToolClient):
    """Read only an explicitly allowlisted issuer domain; no crawler state is persisted."""

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            url = _input_str(request, "url", "")
            allowed = _input_list(request, "official_domains")
            host = urlparse(url).hostname or ""
            if (
                not url.startswith("https://")
                or not allowed
                or not any(
                    host == item.lower() or host.endswith("." + item.lower()) for item in allowed
                )
            ):
                return self._failure(
                    request,
                    code="invalid_official_domain",
                    message=(
                        "url must be HTTPS and match an explicitly supplied "
                        "official_domains allowlist."
                    ),
                )
            text = self._get_text(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; DoxAgent/0.1; +https://example.com/contact)"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
                cache_ttl=int(getattr(self.settings, "sec_cache_ttl_seconds", 900)),
            )
            candidate_urls = _official_candidate_links(text, url)
            return self._success(
                request,
                output={
                    "provider": "issuer_ir",
                    "official_domain": host,
                    "discovery_url": url,
                    "candidate_urls": candidate_urls,
                    "read_only": True,
                },
                raw=None,
                source_kind="issuer_official_site",
                source_id=f"issuer_ir:discovery:{host}",
                title="Issuer IR feed discovery",
                summary="Read an allowlisted issuer page and returned candidate feed references.",
                source_scope="ir_official_feed_discovery",
                confidence=0.7,
                metadata={"url": url, "allowlist_size": len(allowed), "state_written": False},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class IrOfficialUpdatesClient(IrOfficialFeedDiscoveryClient):
    def call(self, request: ToolRequest) -> ToolResult:
        result = super().call(request)
        if result.output:
            result.output["record_type"] = "official_issuer_update"
            result.output["updates"] = result.output.get("candidate_urls", [])[:25]
            result.output["source_coordinates"]["source_scope"] = "ir_official_updates"
        return result


def _records(raw: JsonObject) -> list[object]:
    for key in (
        "results",
        "data",
        "items",
        "documents",
        "awards",
        "opportunitiesData",
        "bills",
        "committeeReports",
        "hearings",
    ):
        value = raw.get(key)
        if isinstance(value, list):
            return value
    if raw and "error" not in raw and "errors" not in raw:
        return [raw]
    return []


_PUBLIC_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("usaspending", "award_search"): (
        "Award ID",
        "generated_internal_id",
        "Recipient Name",
        "Award Amount",
        "Start Date",
        "End Date",
        "Awarding Agency",
        "Awarding Sub Agency",
        "Contract Award Type",
    ),
    ("usaspending", "award_detail"): (
        "generated_unique_award_id",
        "display_award_id",
        "description",
        "recipient",
        "total_obligation",
        "total_outlay",
        "period_of_performance",
        "awarding_agency",
        "funding_agency",
        "naics",
        "psc_hierarchy",
        "latest_transaction_contract_data",
    ),
    ("sam", "contract_opportunity"): (
        "noticeId",
        "title",
        "solicitationNumber",
        "department",
        "subtier",
        "office",
        "postedDate",
        "type",
        "baseType",
        "archiveDate",
        "naicsCode",
        "classificationCode",
        "active",
        "award",
        "uiLink",
        "resourceLinks",
    ),
    ("federal_register", "federal_register_document"): (
        "document_number",
        "title",
        "type",
        "abstract",
        "publication_date",
        "effective_on",
        "agencies",
        "citation",
        "html_url",
        "pdf_url",
        "json_url",
    ),
    ("congress", "congress_bill"): (
        "congress",
        "type",
        "number",
        "title",
        "originChamber",
        "introducedDate",
        "updateDate",
        "latestAction",
        "url",
    ),
    ("congress", "congress_committee-report"): (
        "citation",
        "congress",
        "number",
        "title",
        "updateDate",
        "url",
    ),
    ("congress", "congress_hearing"): (
        "chamber",
        "congress",
        "date",
        "jacketNumber",
        "title",
        "updateDate",
        "url",
    ),
}

_REGULATION_ATTRIBUTE_FIELDS = (
    "title",
    "documentType",
    "postedDate",
    "agencyId",
    "docketId",
    "frDocNum",
    "commentStartDate",
    "commentEndDate",
    "withdrawn",
    "objectId",
)
_OPENFDA_FIELDS = (
    "application_number",
    "device_name",
    "trade_name",
    "generic_name",
    "applicant",
    "decision_date",
    "decision_code",
    "clearance_type",
    "product_code",
    "state",
    "country_code",
    "status",
    "classification",
    "recall_number",
    "recalling_firm",
    "reason_for_recall",
    "product_description",
    "event_date_initiated",
    "report_date",
    "termination_date",
    "distribution_pattern",
    "openfda",
)


def _project_public_records(
    provider: str, record_type: str, records: list[object]
) -> list[JsonObject]:
    projected: list[JsonObject] = []
    fields = _PUBLIC_FIELDS.get((provider, record_type))
    for raw_record in records[:100]:
        if not isinstance(raw_record, dict):
            continue
        if provider == "regulations":
            attributes = raw_record.get("attributes")
            item: JsonObject = {
                "id": raw_record.get("id"),
                "type": raw_record.get("type"),
            }
            if isinstance(attributes, dict):
                item["attributes"] = {
                    key: attributes[key]
                    for key in _REGULATION_ATTRIBUTE_FIELDS
                    if attributes.get(key) not in (None, "", [], {})
                }
            links = raw_record.get("links")
            if isinstance(links, dict) and links.get("self"):
                item["url"] = links["self"]
        elif provider == "openfda":
            item = {
                key: raw_record[key]
                for key in _OPENFDA_FIELDS
                if raw_record.get(key) not in (None, "", [], {})
            }
            openfda = item.get("openfda")
            if isinstance(openfda, dict):
                item["openfda"] = {
                    key: openfda[key]
                    for key in (
                        "device_name",
                        "generic_name",
                        "manufacturer_name",
                        "product_code",
                        "device_class",
                        "application_number",
                        "brand_name",
                        "substance_name",
                    )
                    if openfda.get(key) not in (None, "", [], {})
                }
        elif fields:
            item = {
                key: raw_record[key]
                for key in fields
                if raw_record.get(key) not in (None, "", [], {})
            }
        else:
            item = dict(raw_record)
        if item:
            projected.append(item)
    return projected


class _OfficialLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._label = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._label.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(part for part in self._label if part)))
            self._href, self._label = None, []


def _official_candidate_links(html_text: str, page_url: str) -> list[JsonObject]:
    parser = _OfficialLinkParser()
    parser.feed(html_text)
    selected: list[JsonObject] = []
    seen: set[str] = set()
    tokens = ("press", "release", "news", "event", "earnings", "financial", "rss", "investor")
    for href, label in parser.links:
        absolute = urljoin(page_url, href)
        haystack = f"{absolute} {label}".lower()
        if absolute in seen or not any(token in haystack for token in tokens):
            continue
        seen.add(absolute)
        selected.append({"url": absolute, "label": label[:300] or None})
        if len(selected) >= 100:
            break
    return selected
