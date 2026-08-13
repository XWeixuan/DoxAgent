"""Read-only semantic clients for public procurement, regulation and issuer IR.

These clients intentionally expose records with source coordinates, rather than
turning narrative documents into investment conclusions.  All credentials are
read from settings by the central configuration layer; this module never logs a
credential or places one in ``ToolResult``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from doxagent.models import ResultStatus
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
    _strip_none,
)
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


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
        query: str = "",
        applied_filters: JsonObject | None = None,
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
            if query:
                records = [record for record in records if _record_matches_query(record, query)]
            output = {
                "provider": self.provider,
                "record_type": record_type,
                "records": records,
                "record_count": len(records),
                "normalized_query": query or None,
                "applied_filters": applied_filters or params or {},
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
        except ProviderHttpError as exc:
            if self.provider == "openfda" and exc.code == "not_found":
                return self._partial(
                    request,
                    output={
                        "provider": self.provider,
                        "record_type": record_type,
                        "records": [],
                        "record_count": 0,
                        "applied_filters": applied_filters or params or {},
                    },
                    raw={"records": []},
                    source_kind="official_public_record",
                    source_id=source_id or f"{self.provider}:{record_type}",
                    title=f"{self.provider} {record_type}",
                    summary="openFDA returned no matching records.",
                    source_scope=scope,
                    confidence=0.7,
                    metadata={"endpoint": path, "query_present": bool(params)},
                    code="empty_result",
                    message="No matching openFDA records were returned.",
                    details={"provider_status_code": 404},
                )
            return self._handle_exception(request, exc)
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
        if not filters.get("time_period"):
            return self._failure(
                request,
                code="invalid_input",
                message=(
                    "filters.time_period with start_date and end_date is required; flat date "
                    "fields are not silently accepted."
                ),
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
            output = {
                    "provider": self.provider,
                    "record_type": "award_search",
                    "records": records,
                    "page_metadata": raw.get("page_metadata", {}),
                    "applied_filters": filters,
                }
            kwargs = dict(
                output=output,
                raw={"records": records},
                source_kind="official_public_record",
                source_id="usaspending:award_search",
                title="USAspending award search",
                summary=f"Retrieved {len(records)} federal award records.",
                source_scope="usaspending_award_search",
                confidence=0.9,
                metadata={
                    "endpoint": "/search/spending_by_award/",
                    "page": body["page"],
                    "applied_filters": filters,
                },
            )
            if not records:
                return self._partial(
                    request,
                    code="empty_result",
                    message="USAspending returned no awards for the applied filters.",
                    details={"applied_filters": filters},
                    **kwargs,
                )
            return self._success(request, **kwargs)
        except Exception as exc:
            return self._handle_exception(request, exc)


class UsaSpendingAwardDetailClient(_PublicJsonClient):
    provider, base_url = "usaspending", "https://api.usaspending.gov/api/v2"

    def call(self, request: ToolRequest) -> ToolResult:
        award_id = _input_str(
            request,
            "generated_internal_id",
            _input_str(request, "award_id", ""),
        )
        if not award_id:
            return self._failure(
                request,
                code="invalid_input",
                message=(
                    "generated_internal_id is required "
                    "(award_id remains a compatibility alias)."
                ),
            )
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
        requested_window = {"posted_from": posted_from, "posted_to": posted_to}
        window_adjustment: JsonObject | None = None
        try:
            parsed_from = datetime.strptime(posted_from, "%m/%d/%Y")
            parsed_to = datetime.strptime(posted_to, "%m/%d/%Y")
        except ValueError:
            return self._failure(
                request,
                code="invalid_input",
                message="SAM.gov dates must use MM/DD/YYYY.",
            )
        if parsed_from > parsed_to:
            return self._failure(
                request,
                code="invalid_input",
                message="posted_from must not be after posted_to.",
            )
        # SAM counts both endpoints, so a nominal 365-day difference is
        # rejected as a >1-year inclusive window. Clamp by one day and expose
        # the exact normalization instead of surfacing a provider HTTP 400.
        if (parsed_to - parsed_from).days >= 365:
            normalized_from = parsed_to - timedelta(days=364)
            posted_from = normalized_from.strftime("%m/%d/%Y")
            window_adjustment = {
                "reason": "sam_max_inclusive_window_365_days",
                "requested": requested_window,
                "applied": {"posted_from": posted_from, "posted_to": posted_to},
            }
        query = _input_str(request, "query", _input_str(request, "q", "")).strip()
        params = self._params(
            request,
            defaults={
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": int(request.input.get("limit", 25)),
            },
        )
        if query:
            params["q"] = query
        result = self._read(
            request,
            "/search",
            params=params,
            record_type="contract_opportunity",
            scope="sam_contract_opportunities",
            query=query,
            applied_filters={
                "posted_from": posted_from,
                "posted_to": posted_to,
                "query": query or None,
                "window_adjustment": window_adjustment,
            },
        )
        if result.output and window_adjustment:
            result.output["window_adjustment"] = window_adjustment
            if result.status is ResultStatus.SUCCEEDED:
                return result.model_copy(
                    update={
                        "status": ResultStatus.PARTIAL,
                        "error": ToolError(
                            code="sam_window_clamped",
                            message=(
                                "SAM.gov rejects a 365-day difference as an oversized inclusive "
                                "window; posted_from was advanced by one day."
                            ),
                            retryable=False,
                            details=window_adjustment,
                        ),
                    }
                )
        return result


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
        query = _input_str(request, "query", "").strip()
        params = self._params(request)
        if query:
            params.setdefault("filter[searchTerm]", query)
        agency_id = _input_str(request, "agency_id", "").strip()
        if agency_id:
            params.setdefault("filter[agencyId]", agency_id)
        posted_from = _input_str(request, "posted_from", "").strip()
        posted_to = _input_str(request, "posted_to", "").strip()
        if posted_from:
            params.setdefault("filter[postedDate][ge]", posted_from)
        if posted_to:
            params.setdefault("filter[postedDate][le]", posted_to)
        return self._read(
            request,
            path,
            params=params,
            record_type=f"rulemaking_{mode}",
            scope="regulations_rulemaking_records",
            query=query,
            applied_filters={
                "mode": mode,
                "query": query or None,
                "agency_id": agency_id or None,
                "posted_from": posted_from or None,
                "posted_to": posted_to or None,
            },
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
        query = _input_str(request, "query", "").strip()
        params = self._params(request)
        updated_from = _input_str(request, "updated_from", "").strip()
        updated_to = _input_str(request, "updated_to", "").strip()
        if updated_from:
            params.setdefault("fromDateTime", updated_from)
        if updated_to:
            params.setdefault("toDateTime", updated_to)
        params.setdefault("limit", int(request.input.get("limit", 50)))
        return self._read(
            request,
            path,
            params=params,
            record_type=f"congress_{resource}",
            scope="congress_legislative_actions",
            query=query,
            applied_filters={
                "resource": resource,
                "query": query or None,
                "updated_from": updated_from or None,
                "updated_to": updated_to or None,
                "date_semantics": "Congress.gov updateDate window, not issue/introduction date",
            },
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
            kwargs = dict(
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
            if not candidate_urls:
                return self._partial(
                    request,
                    code="ir_no_feed_candidates",
                    message=(
                        "The official page was reachable but exposed no feed or release "
                        "candidates."
                    ),
                    retryable=False,
                    details={"url": url},
                    **kwargs,
                )
            return self._success(
                request,
                **kwargs,
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class IrOfficialUpdatesClient(IrOfficialFeedDiscoveryClient):
    def call(self, request: ToolRequest) -> ToolResult:
        result = super().call(request)
        if result.output:
            url = _input_str(request, "url", "")
            text = self._get_text(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; DoxAgent/0.1; +https://example.com/contact)"
                    ),
                    "Accept": "application/rss+xml,application/atom+xml,text/html,*/*",
                },
                cache_ttl=int(getattr(self.settings, "sec_cache_ttl_seconds", 900)),
            )
            updates = _parse_official_feed(text, url)
            resolved_feed_url: str | None = url if updates else None
            allowed = _input_list(request, "official_domains")
            queue = [
                (str(item.get("url") or ""), 1)
                for item in result.output.get("candidate_urls", [])
                if isinstance(item, dict) and item.get("kind") in {"feed", "landing"}
            ]
            seen = {url}
            while not updates and queue and len(seen) <= 6:
                candidate_url, depth = queue.pop(0)
                host = (urlparse(candidate_url).hostname or "").lower()
                if (
                    not candidate_url
                    or candidate_url in seen
                    or not any(
                        host == item.lower() or host.endswith("." + item.lower())
                        for item in allowed
                    )
                ):
                    continue
                seen.add(candidate_url)
                candidate_text = self._get_text(
                    candidate_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (compatible; DoxAgent/0.1; +https://example.com/contact)"
                        ),
                        "Accept": "application/rss+xml,application/atom+xml,text/html,*/*",
                    },
                    cache_ttl=int(getattr(self.settings, "sec_cache_ttl_seconds", 900)),
                )
                updates = _parse_official_feed(candidate_text, candidate_url)
                candidate_links = _official_candidate_links(candidate_text, candidate_url)
                if not updates:
                    discovered_feeds = [
                        (str(item.get("url") or ""), depth + 1)
                        for item in candidate_links
                        if item.get("kind") == "feed"
                    ]
                    if discovered_feeds and depth < 3:
                        queue = discovered_feeds + queue
                        continue
                    updates = [
                        item
                        for item in candidate_links
                        if item.get("kind") == "official_update"
                    ][:25]
                if updates:
                    resolved_feed_url = candidate_url
                    break
                if depth < 3:
                    queue.extend(
                        (str(item.get("url") or ""), depth + 1)
                        for item in candidate_links
                        if item.get("kind") in {"feed", "landing"}
                    )
            if not updates:
                updates = [
                    item
                    for item in result.output.get("candidate_urls", [])
                    if isinstance(item, dict) and item.get("kind") == "official_update"
                ][:25]
            result.output["record_type"] = "official_issuer_update"
            result.output["updates"] = updates
            published = _latest_published_at(updates)
            result.output["published_at"] = published
            result.output["as_of"] = published
            result.output["resolved_feed_url"] = resolved_feed_url
            result.output["source_coordinates"]["source_scope"] = "ir_official_updates"
            if not updates:
                return result.model_copy(
                    update={
                        "status": ResultStatus.PARTIAL,
                        "error": ToolError(
                            code="ir_no_official_updates",
                            message=(
                                "The official page was reachable but contained no dated feed "
                                "entries or specific release links."
                            ),
                            retryable=False,
                        ),
                        "output_summary": (
                            "Official IR page was reachable, but navigation links were excluded "
                            "and no substantive updates remained."
                        ),
                    }
                )
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


def _record_matches_query(record: JsonObject, query: str) -> bool:
    tokens = [token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 3]
    if not tokens:
        return True
    haystack = str(record).lower()
    required = 1 if len(tokens) <= 2 else max(2, len(tokens) // 2)
    return sum(token in haystack for token in tokens) >= required


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
        self.feeds: list[dict[str, str]] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "link":
            rel = str(attributes.get("rel") or "").lower()
            mime = str(attributes.get("type") or "").lower()
            href = attributes.get("href")
            if href and "alternate" in rel and mime in {
                "application/rss+xml",
                "application/atom+xml",
            }:
                self.feeds.append(
                    {"href": href, "mime_type": mime, "title": attributes.get("title") or ""}
                )
        if tag.lower() == "a":
            self._href = attributes.get("href")
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
    for feed in parser.feeds:
        absolute = urljoin(page_url, feed["href"])
        if absolute in seen:
            continue
        seen.add(absolute)
        selected.append(
            {
                "url": absolute,
                "label": feed["title"] or "Official RSS/Atom feed",
                "kind": "feed",
                "mime_type": feed["mime_type"],
                "confidence": 1.0,
            }
        )
    tokens = ("press", "release", "news", "event", "earnings", "financial", "rss", "investor")
    generic_labels = {
        "skip",
        "skip to main content",
        "news",
        "events",
        "stock info",
        "investors",
        "investor relations",
        "financial info",
        "financial reports",
        "sec filings",
        "quarterly results",
        "annual reports and proxies",
        "annual meeting",
    }
    for href, label in parser.links:
        absolute = urljoin(page_url, href)
        haystack = f"{absolute} {label}".lower()
        normalized_label = re.sub(r"\s+", " ", label).strip()
        path = urlparse(absolute).path.lower()
        link_haystack = f"{href} {label}".lower()
        is_feed = any(token in link_haystack for token in ("rss", "atom", "feed"))
        is_landing_redirect = (
            normalized_label.lower() in {"here", "continue", "proceed"}
            and (urlparse(absolute).hostname or "").lower()
            == (urlparse(page_url).hostname or "").lower()
            and path.count("/") >= 2
        )
        is_specific_update = (
            normalized_label.lower() not in generic_labels
            and len(normalized_label) >= 8
            and any(token in haystack for token in ("release", "earnings", "financial", "press"))
            and path.count("/") >= 2
        )
        if absolute in seen or (
            not is_landing_redirect and not any(token in haystack for token in tokens)
        ):
            continue
        if not is_feed and not is_specific_update and not is_landing_redirect:
            continue
        seen.add(absolute)
        selected.append(
            {
                "url": absolute,
                "label": normalized_label[:300] or None,
                "kind": (
                    "feed"
                    if is_feed
                    else "landing"
                    if is_landing_redirect
                    else "official_update"
                ),
                "confidence": 0.9 if is_feed else 0.8 if is_landing_redirect else 0.72,
            }
        )
        if len(selected) >= 50:
            break
    return selected


def _parse_official_feed(text: str, feed_url: str) -> list[JsonObject]:
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return []
    try:
        root = ElementTree.fromstring(stripped)
    except ElementTree.ParseError:
        return []
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name not in {"rss", "feed", "rdf"}:
        return []
    entries: list[JsonObject] = []
    for node in root.iter():
        local_name = node.tag.rsplit("}", 1)[-1].lower()
        if local_name not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        link = ""
        for child in list(node):
            child_name = child.tag.rsplit("}", 1)[-1].lower()
            value = " ".join("".join(child.itertext()).split())
            if child_name == "link":
                link = child.attrib.get("href") or value
            elif child_name in {
                "title",
                "pubdate",
                "published",
                "updated",
                "description",
                "summary",
            }:
                fields[child_name] = value
        title = fields.get("title", "").strip()
        if not title or not link:
            continue
        entries.append(
            {
                "title": title[:500],
                "url": urljoin(feed_url, link),
                "published_at": (
                    fields.get("pubdate")
                    or fields.get("published")
                    or fields.get("updated")
                    or None
                ),
                "summary": (fields.get("description") or fields.get("summary") or "")[:1_000]
                or None,
                "kind": "feed_entry",
            }
        )
        if len(entries) >= 100:
            break
    # IR feeds often mix product/news posts with the much rarer financial
    # releases C1 needs. Keep feed order inside each relevance band, but put
    # earnings/results and scheduled earnings calls ahead of generic news.
    entries.sort(key=_ir_update_priority, reverse=True)
    return entries[:25]


def _latest_published_at(entries: list[JsonObject]) -> str | None:
    values = [str(item.get("published_at")) for item in entries if item.get("published_at")]
    if not values:
        return None

    def sort_key(value: str) -> float:
        try:
            return parsedate_to_datetime(value).timestamp()
        except (TypeError, ValueError):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return float("-inf")

    return max(values, key=sort_key)


def _ir_update_priority(entry: JsonObject) -> int:
    title = str(entry.get("title") or "").lower()
    if any(
        phrase in title
        for phrase in (
            "financial results",
            "quarterly results",
            "earnings release",
            "reports results",
        )
    ):
        return 3
    if any(token in title for token in ("earnings", "quarter", "fiscal results")):
        return 2
    if "conference call" in title or "webcast" in title:
        return 1
    return 0
