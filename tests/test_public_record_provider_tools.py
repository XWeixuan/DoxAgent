from __future__ import annotations

import httpx

from doxagent.models import AgentName, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.public_records import (
    FederalRegisterDocumentsClient,
    IrOfficialFeedDiscoveryClient,
    IrOfficialUpdatesClient,
    OpenFdaApprovalMilestonesClient,
    RegulationsRulemakingRecordsClient,
    SamContractOpportunitiesClient,
    UsaSpendingAwardSearchClient,
)
from doxagent.tools.providers.sec import SecCompanyFinancialsClient, SecIssuerFilingsClient
from doxagent.tools.schema import ToolRequest


def _settings() -> DoxAgentSettings:
    return DoxAgentSettings(sec_user_agent="test@example.com")


def _request(name: str, data: dict[str, object]) -> ToolRequest:
    return ToolRequest(
        tool_name=name, ticker="AAPL", agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH, input=data
    )


def _client(payload: object, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_public_record_clients_keep_official_endpoint_and_coordinates() -> None:
    seen: list[httpx.Request] = []
    client = _client({"results": [{"application_number": "FDA-1"}]}, seen)
    result = OpenFdaApprovalMilestonesClient(_settings(), client=client).call(
        _request("openfda.approval_milestones", {"dataset": "device/510k", "params": {"limit": 1}})
    )
    assert result.status is ResultStatus.SUCCEEDED
    assert seen[0].url.path == "/device/510k.json"
    assert result.output["source_coordinates"]["source_scope"] == "openfda_approval_milestones"


def test_openfda_projection_removes_unrelated_nested_metadata() -> None:
    seen: list[httpx.Request] = []
    client = _client(
        {
            "results": [
                {
                    "application_number": "FDA-1",
                    "openfda": {
                        "device_name": ["Relevant device"],
                        "manufacturer_name": ["Relevant manufacturer"],
                        "unrelated_synonyms": ["large", "irrelevant", "payload"],
                    },
                    "unrelated_top_level": "drop me",
                }
            ]
        },
        seen,
    )
    result = OpenFdaApprovalMilestonesClient(_settings(), client=client).call(
        _request("openfda.approval_milestones", {"dataset": "device/510k"})
    )
    record = result.output["records"][0]
    assert set(record) == {"application_number", "openfda"}
    assert set(record["openfda"]) == {"device_name", "manufacturer_name"}


def test_sam_requires_official_date_window_before_request() -> None:
    result = SamContractOpportunitiesClient(_settings()).call(
        _request("sam.contract_opportunities", {})
    )
    assert result.status is ResultStatus.FAILED
    assert result.error and result.error.code == "invalid_input"


def test_usaspending_search_posts_filters_and_returns_records() -> None:
    seen: list[httpx.Request] = []
    result = UsaSpendingAwardSearchClient(
        _settings(), client=_client({"results": [{"Award ID": "X"}]}, seen)
    ).call(
        _request(
            "usaspending.award_search",
            {
                "filters": {
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": "2026-01-01", "end_date": "2026-01-31"}],
                }
            },
        )
    )
    assert result.status is ResultStatus.SUCCEEDED
    assert seen[0].method == "POST"
    assert seen[0].url.path.endswith("/search/spending_by_award/")


def test_regulations_and_federal_register_use_read_only_endpoints() -> None:
    seen: list[httpx.Request] = []
    regulations = RegulationsRulemakingRecordsClient(
        _settings(), client=_client({"data": [{"id": "D"}]}, seen)
    )
    result = regulations.call(
        _request(
            "regulations.rulemaking_records",
            {"mode": "documents", "params": {"filter[searchTerm]": "chips"}},
        )
    )
    assert result.status is ResultStatus.SUCCEEDED
    assert seen[0].url.path == "/v4/documents"
    federal = FederalRegisterDocumentsClient(
        _settings(), client=_client({"results": [{"document_number": "1"}]}, [])
    )
    assert federal.call(_request("federal_register.documents", {})).succeeded


def test_ir_discovery_refuses_unallowlisted_host_and_is_read_only() -> None:
    denied = IrOfficialFeedDiscoveryClient(_settings()).call(
        _request(
            "ir.official_feed_discovery",
            {"url": "https://evil.example/rss", "official_domains": ["apple.com"]},
        )
    )
    assert denied.status is ResultStatus.FAILED
    seen: list[httpx.Request] = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (
                seen.append(request)
                or httpx.Response(200, text='<a href="/news/earnings.html">Earnings release</a>')
            )
        )
    )
    good = IrOfficialFeedDiscoveryClient(_settings(), client=client).call(
        _request(
            "ir.official_feed_discovery",
            {"url": "https://investor.apple.com/news", "official_domains": ["apple.com"]},
        )
    )
    assert good.succeeded
    assert good.output["read_only"] is True
    assert (
        good.output["candidate_urls"][0]["url"] == "https://investor.apple.com/news/earnings.html"
    )
    assert good.output["source_coordinates"]["state_written"] is False
    requests_before_updates = len(seen)
    updates = IrOfficialUpdatesClient(_settings(), client=client).call(
        _request(
            "ir.official_updates",
            {"url": "https://investor.apple.com/news", "official_domains": ["apple.com"]},
        )
    )
    assert updates.succeeded
    assert updates.output["updates"][0]["label"] == "Earnings release"
    assert len(seen) == requests_before_updates + 1


def test_ir_updates_follow_bounded_official_rss_discovery_chain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("quarterly-results/default.aspx"):
            return httpx.Response(
                200,
                text='<a href="/investor-resources/rss/default.aspx">RSS</a>',
            )
        if path.endswith("rss/default.aspx"):
            return httpx.Response(
                200,
                text=(
                    '<a href="https://news.apple.com/press_release.xml">Press Release RSS Feed</a>'
                ),
            )
        return httpx.Response(
            200,
            text=(
                "<rss><channel><item><title>Quarterly results</title>"
                "<link>https://news.apple.com/releases/q1</link>"
                "<pubDate>Wed, 20 May 2026 20:00:00 GMT</pubDate>"
                "</item></channel></rss>"
            ),
        )

    result = IrOfficialUpdatesClient(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).call(
        _request(
            "ir.official_updates",
            {
                "url": "https://investor.apple.com/quarterly-results/default.aspx",
                "official_domains": ["apple.com"],
            },
        )
    )

    assert result.succeeded
    assert result.output["updates"][0]["title"] == "Quarterly results"
    assert result.output["updates"][0]["published_at"].startswith("Wed, 20 May 2026")
    assert result.output["resolved_feed_url"] == "https://news.apple.com/press_release.xml"


def test_ir_updates_retry_entry_and_skip_failed_candidate() -> None:
    calls: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] = calls.get(path, 0) + 1
        if path == "/start":
            if calls[path] == 1:
                raise httpx.ConnectError("temporary TLS EOF", request=request)
            return httpx.Response(
                200,
                text=('<a href="/bad.xml">Press release RSS</a><a href="/good.xml">News RSS</a>'),
            )
        if path == "/bad.xml":
            return httpx.Response(503, text="temporary outage")
        return httpx.Response(
            200,
            text=(
                "<rss><channel><item><title>Quarterly results</title>"
                "<link>https://investor.apple.com/releases/q1</link>"
                "<pubDate>Wed, 20 May 2026 20:00:00 GMT</pubDate>"
                "</item></channel></rss>"
            ),
        )

    result = IrOfficialUpdatesClient(
        _settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ).call(
        _request(
            "ir.official_updates",
            {
                "url": "https://investor.apple.com/start",
                "official_domains": ["apple.com"],
            },
        )
    )

    assert result.status is ResultStatus.PARTIAL
    assert result.output["updates"][0]["title"] == "Quarterly results"
    assert result.output["resolved_feed_url"] == "https://investor.apple.com/good.xml"
    assert result.error and result.error.code == "ir_partial_candidate_failure"
    assert calls == {"/start": 2, "/bad.xml": 2, "/good.xml": 1}


def test_sec_issuer_filings_preserves_legacy_cik_resolution_path() -> None:
    seen: list[httpx.Request] = []
    payload = {
        "name": "Apple",
        "filings": {
            "recent": {
                "form": ["10-K", "8-K"],
                "accessionNumber": ["1", "2"],
                "filingDate": ["2026-01-01", "2026-01-02"],
                "reportDate": ["2025-12-31", ""],
                "primaryDocument": ["a.htm", "b.htm"],
            }
        },
    }
    result = SecIssuerFilingsClient(_settings(), client=_client(payload, seen)).call(
        _request("sec.issuer_filings", {"cik": "320193", "forms": ["10-K"]})
    )
    assert result.succeeded
    assert result.output["filings"][0]["form"] == "10-K"
    assert "CIK0000320193.json" in str(seen[0].url)


def test_sec_requested_financial_concept_is_bounded_and_excludes_fact_pages() -> None:
    seen: list[httpx.Request] = []
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "description": "A long definition that is not needed in a requested preview.",
                    "units": {
                        "USD": [
                            {
                                "accn": f"accn-{index}",
                                "end": f"2026-{index + 1:02d}-01",
                                "form": "10-Q",
                                "fp": "Q1",
                                "val": index,
                            }
                            for index in range(12)
                        ]
                    },
                },
                "UnrequestedFact": {"units": {"USD": [{"val": 999}]}},
            }
        }
    }
    result = SecCompanyFinancialsClient(_settings(), client=_client(payload, seen)).call(
        _request("sec.company_financials", {"cik": "320193", "concepts": ["Revenues"]})
    )
    assert result.succeeded
    assert "fact_pages" not in result.output
    assert result.output["requested_concepts"] == ["Revenues"]
    assert len(result.output["key_facts"][0]["latest_observations"]) == 8
    assert result.output["key_facts"][0]["concept"] == "Revenues"
